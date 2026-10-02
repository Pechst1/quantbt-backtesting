from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource

_INTERVALS: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
    "1wk": (1, "week"),
    "1mo": (1, "month"),
}


class MassiveSource(DataSource):
    """
    Massive (formerly Polygon.io) aggregates.

    `close` is split-adjusted only (like Yahoo with auto_adjust=False) and
    `adj_close` is additionally adjusted for cash dividends, so the handler's
    adj_close/close ratio yields total-return prices.

    Authentication: the API key is read from MASSIVE_API_KEY (or POLYGON_API_KEY)
    and sent as a Bearer header. When neither is set, requests go out without a
    key, which works when the environment's proxy injects the credential.
    """

    name = "massive"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.massive.com",
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _get_json(self, url: str) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "quantbt"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == self.max_retries:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"Massive request failed ({exc.code}): {body}") from exc
            except urllib.error.URLError as exc:
                if attempt == self.max_retries:
                    raise RuntimeError(f"Massive request failed: {exc.reason}") from exc
            time.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    def _paginate(self, path: str, params: dict[str, str]) -> list[dict]:
        url: str | None = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        results: list[dict] = []
        while url:
            payload = self._get_json(url)
            status = str(payload.get("status", "")).upper()
            if status == "ERROR" or status == "NOT_AUTHORIZED":
                raise RuntimeError(f"Massive error for {path}: {payload.get('error') or payload.get('message')}")
            results.extend(payload.get("results") or [])
            url = payload.get("next_url")
        return results

    def _fetch_aggs(self, symbol: str, start: datetime, end: datetime, interval: str, adjusted: bool) -> pd.DataFrame:
        if interval not in _INTERVALS:
            raise ValueError(f"Unsupported interval for Massive: {interval}")
        multiplier, timespan = _INTERVALS[interval]
        start_s = pd.Timestamp(start).strftime("%Y-%m-%d")
        end_s = pd.Timestamp(end).strftime("%Y-%m-%d")
        path = f"/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/{multiplier}/{timespan}/{start_s}/{end_s}"
        rows = self._paginate(path, {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": "50000"})
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)

        frame = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        index = pd.to_datetime(frame["t"], unit="ms", utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        if timespan in {"day", "week", "month"}:
            index = index.dt.normalize()
        frame.index = pd.DatetimeIndex(index)
        frame = frame[["open", "high", "low", "close", "volume"]].astype(float)
        return frame[~frame.index.duplicated(keep="last")].sort_index()

    def fetch_dividends(self, symbol: str) -> pd.Series:
        """Cash dividends per share as declared (not split-adjusted), indexed by ex-dividend date."""
        rows = self._paginate("/v3/reference/dividends", {"ticker": symbol, "limit": "1000"})
        records = [
            (pd.Timestamp(row["ex_dividend_date"]), float(row["cash_amount"]))
            for row in rows
            if row.get("ex_dividend_date") and row.get("cash_amount")
        ]
        if not records:
            return pd.Series(dtype=float)
        series = pd.Series([amount for _, amount in records], index=[date for date, _ in records], dtype=float)
        return series.groupby(level=0).sum().sort_index()

    @staticmethod
    def dividend_adjustment(raw_close: pd.Series, dividends: pd.Series) -> pd.Series:
        """
        Multiplicative factor per bar so that close * factor is the total-return price.

        On each ex-date the factor for all earlier bars is scaled by
        1 - dividend / raw close of the previous bar. Using raw (unadjusted) prices
        keeps the ratio independent of later splits.
        """
        factor = pd.Series(1.0, index=raw_close.index)
        index = raw_close.index
        for ex_date, amount in dividends.items():
            position = index.searchsorted(ex_date)
            if position == 0 or position >= len(index):
                continue
            previous_close = raw_close.iloc[position - 1]
            if not np.isfinite(previous_close) or previous_close <= 0 or amount >= previous_close:
                continue
            factor.iloc[:position] *= 1.0 - amount / previous_close
        return factor

    @staticmethod
    def _map_daily_factor(daily_factor: pd.Series, bar_index: pd.DatetimeIndex, timespan: str) -> pd.Series:
        """Pick, for each bar, the daily factor of the trading day whose close ends that bar."""
        if timespan in {"week", "month", "quarter", "year"}:
            # Coarse bars are stamped with the window start; the window closes before the next bar starts.
            boundaries = list(bar_index[1:]) + [daily_factor.index[-1] + pd.Timedelta(days=1)]
        else:
            boundaries = list(bar_index.normalize() + pd.Timedelta(days=1))
        positions = daily_factor.index.searchsorted(pd.DatetimeIndex(boundaries), side="left") - 1
        values = daily_factor.to_numpy()[np.clip(positions, 0, len(daily_factor) - 1)]
        values[positions < 0] = daily_factor.iloc[0]
        return pd.Series(values, index=bar_index)

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        frame = self._fetch_aggs(symbol, start, end, interval, adjusted=True)
        timespan = _INTERVALS[interval][1]
        if timespan in {"minute", "hour"}:
            # The endpoint is queried by calendar date; honor intraday bounds explicitly.
            start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
            frame = frame[frame.index >= start_ts]
            if end_ts != end_ts.normalize():
                frame = frame[frame.index <= end_ts]
        if frame.empty:
            raise ValueError(f"No data returned from Massive for {symbol}.")

        dividends = self.fetch_dividends(symbol)
        if dividends.empty:
            frame["adj_close"] = frame["close"]
        else:
            # Dividend factors are always computed on daily raw closes, then mapped onto the bars.
            daily_start = min(pd.Timestamp(start), frame.index[0])
            raw_daily = self._fetch_aggs(symbol, daily_start, end, "1d", adjusted=False)
            daily_factor = self.dividend_adjustment(raw_daily["close"], dividends)
            frame["adj_close"] = frame["close"] * self._map_daily_factor(daily_factor, frame.index, timespan)

        return frame[["open", "high", "low", "close", "adj_close", "volume"]]
