from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler


@dataclass(slots=True)
class PricePanel:
    """Total-return adjusted OHLC on a shared calendar.

    A symbol has NaN on days it did not trade (before inception or on a missing
    bar); simulators must not fill orders on those days.
    """

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> PricePanel:
        if not frames:
            raise ValueError("No price frames given.")
        index = pd.DatetimeIndex(sorted(set().union(*[set(f.index) for f in frames.values()])))
        fields = {}
        for name in ("open", "high", "low", "close"):
            fields[name] = pd.DataFrame(
                {symbol: frame[name].reindex(index) for symbol, frame in frames.items()},
                index=index,
                dtype=float,
            )
        return cls(**fields)


def _read_csv_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame.columns = [str(col).strip().lower().replace(" ", "_") for col in frame.columns]
    date_col = "date" if "date" in frame.columns else frame.columns[0]
    frame.index = pd.to_datetime(frame[date_col]).dt.tz_localize(None)
    return frame.drop(columns=[date_col])


def _clean(frame: pd.DataFrame, adjust_prices: bool = True) -> pd.DataFrame:
    """Same cleaning and dividend adjustment as the event engine, without forward-filled bars."""
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for col in ("open", "high", "low", "close", "adj_close", "volume"):
        if col not in frame.columns:
            frame[col] = np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)
    for col in ("open", "high", "low", "close", "adj_close"):
        frame.loc[frame[col] <= 0.0, col] = np.nan
    frame = frame[frame["close"].notna()]
    frame["adj_close"] = frame["adj_close"].fillna(frame["close"])
    for col in ("open", "high", "low"):
        frame[col] = frame[col].fillna(frame["close"])
    frame["high"] = frame[["open", "high", "low", "close"]].max(axis=1)
    frame["low"] = frame[["open", "high", "low", "close"]].min(axis=1)
    if adjust_prices:
        ratio = (frame["adj_close"] / frame["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col] * ratio
    return frame[["open", "high", "low", "close"]]


def load_panel(
    symbols: list[str],
    start: datetime,
    end: datetime,
    *,
    data_dir: Path | None = None,
    cache_dir: Path = Path(".cache/market_data"),
    strict: bool = False,
) -> PricePanel:
    """Load daily bars from `data_dir/<SYMBOL>.csv` if given, else Yahoo via the repo's cached source."""
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for symbol in symbols:
        try:
            if data_dir is not None:
                path = Path(data_dir) / f"{symbol}.csv"
                raw = _read_csv_frame(path)
            else:
                handler = PublicOHLCVDataHandler(
                    DataHandlerConfig(symbols=[symbol], start=start, end=end, cache_dir=cache_dir, strict_symbols=True)
                )
                raw = handler._load_symbol(symbol)
        except Exception:
            if strict:
                raise
            missing.append(symbol)
            continue
        cleaned = _clean(raw)
        cleaned = cleaned[(cleaned.index >= pd.Timestamp(start)) & (cleaned.index <= pd.Timestamp(end))]
        if cleaned.empty:
            missing.append(symbol)
            continue
        frames[symbol] = cleaned
    if missing and strict:
        raise ValueError(f"Missing data for {missing}")
    return PricePanel.from_frames(frames)


def daily_cash_rate(panel: PricePanel, cash_symbol: str | None) -> pd.Series:
    """Daily T-bill return proxied by a bill ETF's total return; zero where unavailable."""
    if cash_symbol is None or cash_symbol not in panel.close.columns:
        return pd.Series(0.0, index=panel.index)
    return panel.close[cash_symbol].ffill().pct_change().fillna(0.0)
