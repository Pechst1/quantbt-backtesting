from __future__ import annotations

from datetime import datetime

import pandas as pd

from quantbt.data.base import DataSource

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise RuntimeError(
        "The 'yfinance' package is required for YahooFinanceSource. "
        "Install dependencies with: pip install -e ."
    ) from exc


class YahooFinanceSource(DataSource):
    name = "yahoo_finance"

    @staticmethod
    def _normalize_ohlcv_columns(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if isinstance(frame.columns, pd.MultiIndex):
            for level in range(frame.columns.nlevels):
                if symbol in frame.columns.get_level_values(level):
                    frame = frame.xs(symbol, axis=1, level=level, drop_level=True)
                    break

        if isinstance(frame.columns, pd.MultiIndex):
            flattened: list[str] = []
            for col in frame.columns:
                parts = [str(x).strip().lower().replace(" ", "_") for x in col if x is not None]
                selected = ""
                for part in parts:
                    if part in {"open", "high", "low", "close", "adj_close", "adjclose", "volume"}:
                        selected = part
                        break
                if not selected and parts:
                    selected = parts[-1]
                flattened.append(selected)
            frame.columns = flattened
        else:
            frame.columns = [str(col).strip().lower().replace(" ", "_") for col in frame.columns]

        frame = frame.rename(columns={"adjclose": "adj_close"})
        frame = frame.loc[:, ~pd.Index(frame.columns).duplicated(keep="first")]
        return frame

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        frame = yf.download(
            tickers=symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=False,
            progress=False,
            actions=True,
        )
        if frame.empty:
            raise ValueError(f"No data returned from Yahoo Finance for {symbol}.")

        frame = self._normalize_ohlcv_columns(frame, symbol=symbol)

        for col in ("open", "high", "low", "close", "adj_close", "volume"):
            if col not in frame.columns:
                frame[col] = pd.NA

        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame = frame[["open", "high", "low", "close", "adj_close", "volume"]]
        return frame
