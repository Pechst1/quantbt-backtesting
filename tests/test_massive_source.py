from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from quantbt.data.massive import MassiveSource


def _bar(day: str, close: float) -> dict:
    ts = pd.Timestamp(day, tz="America/New_York").tz_convert("UTC")
    return {"t": int(ts.value // 1_000_000), "o": close, "h": close, "l": close, "c": close, "v": 100.0}


class FakeMassive(MassiveSource):
    """2:1 split on 2024-01-04 and a $1.00 (pre-split) dividend going ex on 2024-01-03."""

    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.urls: list[str] = []

    def _get_json(self, url: str) -> dict:
        self.urls.append(url)
        if "/v3/reference/dividends" in url:
            return {"status": "OK", "results": [{"ex_dividend_date": "2024-01-03", "cash_amount": 1.0}]}
        if "adjusted=false" in url:
            bars = [_bar("2024-01-02", 100.0), _bar("2024-01-03", 99.0), _bar("2024-01-04", 50.0)]
        else:
            bars = [_bar("2024-01-02", 50.0), _bar("2024-01-03", 49.5), _bar("2024-01-04", 50.0)]
        if "page2" in url:
            return {"status": "OK", "results": bars[2:]}
        return {"status": "OK", "results": bars[:2], "next_url": "https://api.massive.com/next?page2=1"}


def test_fetch_ohlcv_paginates_and_adjusts_dividends():
    source = FakeMassive()
    frame = source.fetch_ohlcv("XYZ", datetime(2024, 1, 2), datetime(2024, 1, 4), "1d")

    assert list(frame.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert list(frame.index) == list(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    assert frame["close"].tolist() == [50.0, 49.5, 50.0]
    # Only the bar before the ex-date is scaled, by 1 - 1.00 / 100.00 on raw prices.
    assert frame["adj_close"].tolist() == pytest.approx([49.5, 49.5, 50.0])
    assert any("adjusted=true" in url for url in source.urls)


def test_no_dividends_skips_raw_request():
    class NoDividends(FakeMassive):
        def _get_json(self, url: str) -> dict:
            if "/v3/reference/dividends" in url:
                self.urls.append(url)
                return {"status": "OK", "results": []}
            return super()._get_json(url)

    source = NoDividends()
    frame = source.fetch_ohlcv("XYZ", datetime(2024, 1, 2), datetime(2024, 1, 4), "1d")
    assert frame["adj_close"].tolist() == frame["close"].tolist()
    assert not any("adjusted=false" in url for url in source.urls)


def test_unsupported_interval():
    with pytest.raises(ValueError):
        FakeMassive().fetch_ohlcv("XYZ", datetime(2024, 1, 2), datetime(2024, 1, 4), "2d")


class RoutedMassive(MassiveSource):
    def __init__(self, routes: dict[str, list[dict]], dividends: list[dict]) -> None:
        super().__init__(api_key="test")
        self.routes = routes
        self.dividends = dividends

    def _get_json(self, url: str) -> dict:
        if "/v3/reference/dividends" in url:
            return {"status": "OK", "results": self.dividends}
        for key, rows in self.routes.items():
            if key in url:
                return {"status": "OK", "results": rows}
        raise AssertionError(f"unexpected url {url}")


def test_weekly_bars_take_dividend_in_the_week_it_goes_ex():
    daily = [_bar(day, 100.0) for day in pd.bdate_range("2024-01-01", "2024-01-12").strftime("%Y-%m-%d")]
    weekly = [_bar("2024-01-01", 100.0), _bar("2024-01-08", 99.0)]
    source = RoutedMassive(
        {"/range/1/week/": weekly, "/range/1/day/": daily},
        [{"ex_dividend_date": "2024-01-10", "cash_amount": 1.0}],
    )
    frame = source.fetch_ohlcv("XYZ", datetime(2024, 1, 1), datetime(2024, 1, 12), "1wk")
    # Week 1 closes before the Wednesday ex-date and is scaled; week 2 closes after it and is not.
    assert frame["adj_close"].tolist() == pytest.approx([99.0, 99.0])


def test_intraday_bounds_are_respected():
    hours = ["2024-01-02 09:00", "2024-01-02 12:00", "2024-01-02 15:00", "2024-01-02 16:00"]
    rows = []
    for stamp in hours:
        ts = pd.Timestamp(stamp, tz="America/New_York").tz_convert("UTC")
        rows.append({"t": int(ts.value // 1_000_000), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0})
    source = RoutedMassive({"/range/1/hour/": rows}, [])
    frame = source.fetch_ohlcv("XYZ", datetime(2024, 1, 2, 12), datetime(2024, 1, 2, 15), "1h")
    assert list(frame.index) == list(pd.to_datetime(["2024-01-02 12:00", "2024-01-02 15:00"]))
