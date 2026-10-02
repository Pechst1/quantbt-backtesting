from __future__ import annotations

import urllib.request
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


def load_fred_rate(series: str = "DTB3", cache_dir: str | Path = ".cache/fred") -> pd.Series:
    """Daily annualized rate as a decimal (e.g. 0.05), forward-filled over FRED's gaps."""
    path = Path(cache_dir) / f"{series}.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FRED_CSV_URL.format(series=series), path)
    frame = pd.read_csv(path)
    frame.columns = ["date", "value"]
    values = pd.to_numeric(frame["value"], errors="coerce") / 100.0
    rate = pd.Series(values.to_numpy(), index=pd.to_datetime(frame["date"]), name=series)
    return rate.sort_index().ffill().dropna()


def rate_lookup(rate: pd.Series, offset: float = 0.0):
    """Callable for Portfolio(cash_interest_rate=...): last published rate on or before a date."""
    dates = rate.index
    values = rate.to_numpy()

    def lookup(as_of: date) -> float:
        k = dates.searchsorted(pd.Timestamp(as_of), side="right") - 1
        return max(float(values[k]) + offset, 0.0) if k >= 0 else 0.0

    return lookup


def total_return_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    """Scale open/high/low/close by adj_close/close so close-to-close returns include dividends."""
    factor = (frame["adj_close"] / frame["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out = frame[["open", "high", "low", "close"]].mul(factor, axis=0)
    out["adj_close"] = out["close"]
    out["volume"] = frame["volume"]
    return out


def synthetic_leveraged_ohlc(
    underlying: pd.DataFrame,
    leverage: float,
    rate: pd.Series,
    expense_ratio: float = 0.0095,
    financing_spread: float = 0.005,
    start_price: float = 100.0,
) -> pd.DataFrame:
    """
    Daily-reset leveraged ETF built from a total-return OHLC frame, like SSO/UPRO/QLD/TQQQ.

    Close-to-close: r_L = L * r - [(L - 1) * (rate + financing_spread) + expense_ratio] * days / 365,
    where days counts calendar days since the previous close and rate is the last value
    published before today. The fund's intraday path is L times the underlying's move from the
    previous close, so open/high/low are mapped the same way (costs only hit the close).
    """
    if leverage <= 0:
        raise ValueError("Only long leveraged funds are modelled.")
    tr = total_return_ohlc(underlying)
    prev_close = tr["close"].shift(1)
    days = tr.index.to_series().diff().dt.days.fillna(0.0).to_numpy()
    prior_rate = rate.reindex(tr.index, method="ffill").shift(1).bfill().fillna(0.0).to_numpy()
    carry = ((leverage - 1.0) * (prior_rate + financing_spread) + expense_ratio) * days / 365.0

    ret = (tr["close"] / prev_close - 1.0).fillna(0.0).to_numpy()
    growth = np.maximum(1.0 + leverage * ret - carry, 0.0)
    close = start_price * np.cumprod(growth)
    prev = np.concatenate([[start_price], close[:-1]])

    def mapped(col: str) -> np.ndarray:
        move = (tr[col] / prev_close - 1.0).fillna(0.0).to_numpy()
        return prev * np.maximum(1.0 + leverage * move, 0.0)

    open_ = mapped("open")
    high = np.maximum.reduce([mapped("high"), open_, close])
    low = np.minimum.reduce([mapped("low"), open_, close])
    out = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "adj_close": close, "volume": tr["volume"].to_numpy()},
        index=tr.index,
    )
    # A fund that hits zero stays at zero; keep a tiny positive price so the engine can mark it.
    return out.clip(lower=1e-6)


class FrameSource(DataSource):
    """Serves pre-built daily frames (e.g. synthetic leveraged ETFs) to the data handler."""

    name = "frames"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = {symbol.upper(): frame.sort_index() for symbol, frame in frames.items()}

    @property
    def symbols(self) -> list[str]:
        return sorted(self._frames)

    def fetch_ohlcv(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("FrameSource only holds daily bars.")
        frame = self._frames.get(symbol.upper())
        if frame is None:
            raise ValueError(f"No frame for {symbol}.")
        sliced = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
        if sliced.empty:
            raise ValueError(f"No data for {symbol} between {start} and {end}.")
        return sliced.copy()
