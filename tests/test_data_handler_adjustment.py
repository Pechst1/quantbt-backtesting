from __future__ import annotations

from datetime import datetime

import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler


class SyntheticSource(DataSource):
    name = "synthetic_adjust"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        return self.frame.copy()


class FailingSource(DataSource):
    name = "synthetic_adjust"

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        raise AssertionError("source fetch should not be called when a covering cache exists")


def test_adjusted_close_scaling(tmp_path):
    idx = pd.date_range("2024-01-01", periods=2, freq="D")
    frame = pd.DataFrame(
        {
            "open": [100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [100.0, 200.0],
            "adj_close": [50.0, 100.0],
            "volume": [1000.0, 1500.0],
        },
        index=idx,
    )

    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA"],
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 3),
            interval="1d",
            adjust_prices=True,
            cache_dir=tmp_path,
        ),
        source=SyntheticSource(frame=frame),
    )

    event = handler.stream_next()
    bar = event.bars["AAA"]
    assert bar.close == 50.0
    assert bar.open == 50.0
    assert bar.high == 55.0
    assert bar.low == 45.0


def test_duplicate_ohlcv_columns_are_collapsed(tmp_path):
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    base = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "adj_close": [100.5, 101.5, 102.5],
            "volume": [1000.0, 1100.0, 1200.0],
        },
        index=idx,
    )
    duplicate_close = base["close"] + 0.25
    duplicate_close.name = "close"
    frame_with_duplicates = pd.concat([base, duplicate_close], axis=1)

    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA"],
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 5),
            interval="1d",
            adjust_prices=False,
            cache_dir=tmp_path,
        ),
        source=SyntheticSource(frame=frame_with_duplicates),
    )

    event = handler.stream_next()
    assert event.bars["AAA"].close == 100.5


def test_covering_cache_file_is_reused_without_refetch(tmp_path):
    cache_root = tmp_path / "synthetic_adjust"
    cache_root.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range("2020-01-01", "2025-01-01", freq="D")
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "adj_close": 100.0,
            "volume": 1000.0,
        },
        index=idx,
    )
    cache_file = cache_root / "AAA_1d_20180101_20250101.parquet"
    frame.to_parquet(cache_file)

    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA"],
            start=datetime(2020, 1, 1),
            end=datetime(2025, 1, 1),
            interval="1d",
            adjust_prices=False,
            cache_dir=tmp_path,
        ),
        source=FailingSource(),
    )

    event = handler.stream_next()
    assert event.bars["AAA"].close == 100.0
