from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from quantbt.data.base import DataSource


class ParquetPanelSource(DataSource):
    """
    Reads daily OHLCV for many symbols from one long-format parquet file.

    Expected columns: date, ticker, open, high, low, close, volume, adj_close.
    This is the layout of the survivorship-aware S&P 500 dataset published at
    https://github.com/Johnbrick123/sp500-data (see examples/fetch_sp500_panel.py).
    Delisted names carry a "-YYYYMM" suffix there, so a recycled ticker never
    mixes two companies.
    """

    name = "parquet_panel"

    def __init__(self, path: str | Path, symbols: list[str] | None = None) -> None:
        self.path = Path(path)
        columns = ["date", "ticker", "open", "high", "low", "close", "volume", "adj_close"]
        filters = [("ticker", "in", list(symbols))] if symbols else None
        frame = pd.read_parquet(self.path, columns=columns, filters=filters)
        frame["date"] = pd.to_datetime(frame["date"])
        self._frames = {
            str(ticker): group.drop(columns="ticker").set_index("date").sort_index()
            for ticker, group in frame.groupby("ticker", sort=False)
        }

    @property
    def symbols(self) -> list[str]:
        return sorted(self._frames)

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("ParquetPanelSource only holds daily bars.")
        frame = self._frames.get(symbol)
        if frame is None:
            raise ValueError(f"No data for {symbol} in {self.path}.")
        sliced = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
        if sliced.empty:
            raise ValueError(f"No data for {symbol} between {start} and {end}.")
        return sliced.copy()
