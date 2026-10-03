from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.core.events import MarketEvent
from quantbt.core.models import Bar
from quantbt.data.base import DataSource
from quantbt.data.yahoo import YahooFinanceSource


@dataclass(slots=True)
class DataHandlerConfig:
    symbols: list[str]
    start: datetime
    end: datetime
    interval: str = "1d"
    adjust_prices: bool = True
    cache_dir: Path = Path(".cache/market_data")
    strict_symbols: bool = False
    required_symbols: set[str] = field(default_factory=set)
    symbol_metadata: dict[str, dict[str, str | float]] = field(default_factory=dict)


class PublicOHLCVDataHandler:
    def __init__(
        self,
        config: DataHandlerConfig,
        source: DataSource | None = None,
    ) -> None:
        self.config = config
        self.source = source or YahooFinanceSource()
        self.latest_bars: dict[str, Bar] = {}
        self._cursor = 0
        self._frames = self._load_and_align_data()
        self._symbols = list(self._frames.keys())
        self._timestamps = list(self._frames[self._symbols[0]].index)

    def _cache_file(self, symbol: str) -> Path:
        start_s = pd.Timestamp(self.config.start).strftime("%Y%m%d")
        end_s = pd.Timestamp(self.config.end).strftime("%Y%m%d")
        safe_symbol = symbol.replace("/", "_")
        cache_root = self.config.cache_dir / self.source.name
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root / f"{safe_symbol}_{self.config.interval}_{start_s}_{end_s}.parquet"

    def _read_cached_frame(self, path: Path) -> pd.DataFrame:
        frame = pd.read_parquet(path)
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        start = pd.Timestamp(self.config.start)
        end = pd.Timestamp(self.config.end)
        return frame[(frame.index >= start) & (frame.index <= end)].copy()

    def _covering_cache_file(self, symbol: str) -> Path | None:
        safe_symbol = symbol.replace("/", "_")
        cache_root = self.config.cache_dir / self.source.name
        prefix = f"{safe_symbol}_{self.config.interval}_"
        request_start = pd.Timestamp(self.config.start)
        request_end = pd.Timestamp(self.config.end)
        candidates: list[tuple[int, Path]] = []
        for path in cache_root.glob(f"{prefix}*.parquet"):
            suffix = path.stem[len(prefix) :]
            parts = suffix.split("_", 1)
            if len(parts) != 2:
                continue
            start_s, end_s = parts
            try:
                cache_start = pd.Timestamp(datetime.strptime(start_s, "%Y%m%d"))
                cache_end = pd.Timestamp(datetime.strptime(end_s, "%Y%m%d"))
            except ValueError:
                continue
            if cache_start > request_start or cache_end < request_end:
                continue
            span = int((cache_end - cache_start).days)
            candidates.append((span, path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _load_symbol(self, symbol: str) -> pd.DataFrame:
        cache_path = self._cache_file(symbol)
        if cache_path.exists():
            return self._read_cached_frame(cache_path)

        covering_cache = self._covering_cache_file(symbol)
        if covering_cache is not None:
            return self._read_cached_frame(covering_cache)

        frame = self.source.fetch_ohlcv(
            symbol=symbol,
            start=self.config.start,
            end=self.config.end,
            interval=self.config.interval,
        )
        if not frame.columns.is_unique:
            frame = frame.loc[:, ~pd.Index(frame.columns).duplicated(keep="first")]
        frame.to_parquet(cache_path)
        return frame

    def _clean_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        source = frame.copy()
        source.index = pd.to_datetime(source.index).tz_localize(None)
        source = source[~source.index.duplicated(keep="last")]
        source = source.sort_index()

        def normalized_name(value: object) -> str:
            return str(value).strip().lower().replace(" ", "_")

        def extract_column(name: str) -> pd.Series:
            aliases = {name}
            if name == "adj_close":
                aliases.add("adjclose")

            candidates: list[pd.Series] = []
            for column in source.columns:
                if isinstance(column, tuple):
                    parts = {normalized_name(part) for part in column}
                else:
                    parts = {normalized_name(column)}
                if aliases.isdisjoint(parts):
                    continue

                selected = source.loc[:, column]
                if isinstance(selected, pd.DataFrame):
                    selected = selected.bfill(axis=1).iloc[:, 0]
                candidates.append(selected)

            if not candidates:
                return pd.Series(np.nan, index=source.index, dtype=float, name=name)
            if len(candidates) == 1:
                collapsed = candidates[0]
            else:
                collapsed = pd.concat(candidates, axis=1).bfill(axis=1).iloc[:, 0]
            return pd.to_numeric(collapsed, errors="coerce").astype(float)

        frame = pd.DataFrame(
            {col: extract_column(col) for col in ("open", "high", "low", "close", "adj_close", "volume")},
            index=source.index,
        )

        # Reject invalid price points and reconstruct from neighboring data.
        for col in ("open", "high", "low", "close", "adj_close"):
            frame.loc[frame[col] <= 0.0, col] = np.nan
        frame.loc[frame["volume"] < 0.0, "volume"] = np.nan

        frame["close"] = frame["close"].ffill().bfill()
        frame["adj_close"] = frame["adj_close"].fillna(frame["close"])
        frame["open"] = frame["open"].fillna(frame["close"])
        frame["high"] = frame["high"].fillna(frame["close"])
        frame["low"] = frame["low"].fillna(frame["close"])
        frame["volume"] = frame["volume"].fillna(0.0)

        # Ensure coherent OHLC relationship after cleaning.
        high_calc = frame[["open", "high", "low", "close"]].max(axis=1)
        low_calc = frame[["open", "high", "low", "close"]].min(axis=1)
        frame["high"] = high_calc
        frame["low"] = low_calc

        if self.config.adjust_prices:
            ratio = frame["adj_close"] / frame["close"]
            ratio = ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0)
            for col in ("open", "high", "low", "close"):
                frame[col] = frame[col] * ratio
            frame["close"] = frame["adj_close"]

        return frame[["open", "high", "low", "close", "adj_close", "volume"]]

    def _align_symbol(self, frame: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
        aligned = frame.reindex(index)
        # Rows the source did not have are marked stale: they keep a price for marking but
        # are not tradable (execution skips them).
        aligned["is_stale"] = aligned["close"].isna()
        # Forward-only fill avoids look-ahead bias before a symbol's first real bar.
        aligned["close"] = aligned["close"].ffill()
        aligned["adj_close"] = aligned["adj_close"].fillna(aligned["close"])
        aligned["open"] = aligned["open"].fillna(aligned["close"])
        aligned["high"] = aligned["high"].fillna(aligned["close"])
        aligned["low"] = aligned["low"].fillna(aligned["close"])
        aligned["volume"] = aligned["volume"].fillna(0.0)
        return aligned

    def _load_and_align_data(self) -> dict[str, pd.DataFrame]:
        raw_frames: dict[str, pd.DataFrame] = {}
        required_symbols = {symbol.upper() for symbol in self.config.required_symbols}
        for symbol in self.config.symbols:
            try:
                fetched = self._load_symbol(symbol)
            except Exception:
                if self.config.strict_symbols or symbol.upper() in required_symbols:
                    raise
                continue
            cleaned = self._clean_frame(fetched)
            if cleaned.empty or not cleaned["close"].notna().any():
                if self.config.strict_symbols or symbol.upper() in required_symbols:
                    raise ValueError(f"No usable OHLCV data for symbol {symbol}.")
                continue
            raw_frames[symbol] = cleaned

        if not raw_frames:
            raise ValueError("No symbols with usable market data were found.")

        full_index = sorted(set().union(*[set(frame.index) for frame in raw_frames.values()]))
        full_index = pd.DatetimeIndex(full_index)
        if full_index.empty:
            raise ValueError("No market data available for the configured symbols.")

        aligned_frames: dict[str, pd.DataFrame] = {}
        for symbol, frame in raw_frames.items():
            aligned_frames[symbol] = self._align_symbol(frame, full_index)
        return aligned_frames

    def has_next(self) -> bool:
        return self._cursor < len(self._timestamps)

    def stream_next(self) -> MarketEvent:
        if not self.has_next():
            raise StopIteration("No more bars available.")

        timestamp = pd.Timestamp(self._timestamps[self._cursor]).to_pydatetime()
        bars: dict[str, Bar] = {}
        for symbol, frame in self._frames.items():
            row = frame.iloc[self._cursor]
            if pd.isna(row["close"]):
                continue
            bar = Bar(
                symbol=symbol,
                timestamp=timestamp,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                adj_close=float(row["adj_close"]),
                is_stale=bool(row.get("is_stale", False)),
            )
            self.latest_bars[symbol] = bar
            bars[symbol] = bar

        self._cursor += 1
        return MarketEvent(timestamp=timestamp, bars=bars)

    def get_latest_bar(self, symbol: str) -> Bar | None:
        return self.latest_bars.get(symbol)

    @property
    def interval(self) -> str:
        return self.config.interval

    @property
    def active_symbols(self) -> list[str]:
        return list(self._symbols)

    def symbol_metadata(self, symbol: str) -> dict[str, str | float]:
        return dict(self.config.symbol_metadata.get(symbol.upper(), {}))

    def sector_for_symbol(self, symbol: str) -> str | None:
        metadata = self.symbol_metadata(symbol)
        value = metadata.get("sector")
        if value is None:
            return None
        return str(value)
