from __future__ import annotations

from datetime import datetime

import pandas as pd

from quantbt.altdata import CSVIndexMembershipDataSource, HistoricalUniverseBuilder
from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategy.base import BaseStrategy


class SyntheticSource(DataSource):
    name = "universe_synth"

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


class AlwaysBuyStrategy(BaseStrategy):
    def on_data(self, market_event):
        return [
            self.buy(
                timestamp=market_event.timestamp,
                symbol=self.symbols[0],
                quantity=1,
            )
        ]


def _simple_frame() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.Series([10.0, 10.1, 10.2, 10.1, 10.0], index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def test_csv_index_membership_and_builders(tmp_path):
    csv_path = tmp_path / "membership.csv"
    csv_path.write_text(
        "\n".join(
            [
                "index_name,symbol,effective_from,effective_to",
                "SP500,AAPL,2020-01-01,",
                "SP500,MSFT,2021-01-01,",
                "RUSSELL2000,PLTR,2022-01-01,",
            ]
        ),
        encoding="utf-8",
    )

    source = CSVIndexMembershipDataSource(csv_path)
    builder = HistoricalUniverseBuilder(source)
    sp500 = builder.sp500()
    r2k = builder.russell2000()

    assert sp500.is_member("AAPL", datetime(2024, 1, 10).date())
    assert not sp500.is_member("PLTR", datetime(2024, 1, 10).date())
    assert r2k.is_member("PLTR", datetime(2024, 1, 10).date())
    assert "SP500" in source.available_indices()


def test_engine_blocks_entries_outside_point_in_time_universe(tmp_path):
    csv_path = tmp_path / "membership.csv"
    csv_path.write_text(
        "\n".join(
            [
                "index_name,symbol,effective_from,effective_to",
                "SP500,BBB,2020-01-01,",
            ]
        ),
        encoding="utf-8",
    )
    universe = HistoricalUniverseBuilder(CSVIndexMembershipDataSource(csv_path)).sp500()

    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA"],
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 10),
            interval="1d",
            adjust_prices=False,
            cache_dir=tmp_path / "cache",
        ),
        source=SyntheticSource(_simple_frame()),
    )
    engine = BacktestEngine(
        data_handler=handler,
        strategy=AlwaysBuyStrategy(symbols=["AAA"]),
        portfolio=Portfolio(initial_cash=10_000.0),
        execution_handler=SimulatedExecutionHandler(ExecutionConfig(slippage_bps=0.0, spread_bps=0.0)),
        universe=universe,
    )
    result = engine.run(run_name="universe_guard")
    assert result.submitted_orders == 0
    assert result.universe_rejections > 0

