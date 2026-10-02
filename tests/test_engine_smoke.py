from __future__ import annotations

from datetime import datetime

import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategy.base import BaseStrategy


class SyntheticSource(DataSource):
    name = "synthetic"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        return self.frames[symbol].copy()


class PartiallyMissingSource(DataSource):
    name = "partial_missing"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> pd.DataFrame:
        if symbol not in self.frames:
            raise ValueError(f"No data for {symbol}")
        return self.frames[symbol].copy()


class SingleRoundTripStrategy(BaseStrategy):
    def __init__(self, symbols: list[str]) -> None:
        super().__init__(symbols=symbols)
        self._i = 0

    def on_data(self, market_event):
        self._i += 1
        out = []
        if self._i == 1:
            out.append(
                self.buy(
                    timestamp=market_event.timestamp,
                    symbol=self.symbols[0],
                    quantity=10,
                )
            )
        elif self._i == 6:
            out.append(
                self.sell(
                    timestamp=market_event.timestamp,
                    symbol=self.symbols[0],
                    quantity=10,
                )
            )
        return out


def _make_frame(start: str, periods: int, base: float) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=periods, freq="D")
    close = pd.Series([base + i for i in range(periods)], index=idx, dtype=float)
    frame = pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 0.7,
            "low": close - 0.8,
            "close": close,
            "adj_close": close,
            "volume": 1_000.0,
        },
        index=idx,
    )
    return frame


def test_engine_smoke(tmp_path):
    frames = {"AAA": _make_frame("2022-01-01", 20, 100.0), "BBB": _make_frame("2022-01-01", 20, 50.0)}
    source = SyntheticSource(frames=frames)
    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA", "BBB"],
            start=datetime(2022, 1, 1),
            end=datetime(2022, 2, 1),
            interval="1d",
            adjust_prices=False,
            cache_dir=tmp_path,
        ),
        source=source,
    )

    strategy = SingleRoundTripStrategy(symbols=["AAA", "BBB"])
    portfolio = Portfolio(
        initial_cash=100_000.0,
        leverage=2.0,
        maintenance_margin_ratio=0.25,
        position_sizer=FixedFractionalSizer(risk_fraction=0.10),
    )
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(
            slippage_bps=1.0,
            spread_bps=2.0,
            commission_fixed=0.0,
            commission_pct=0.0002,
        )
    )
    engine = BacktestEngine(
        data_handler=handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution,
    )
    result = engine.run(run_name="smoke")

    assert result.fills >= 2
    assert result.submitted_orders >= 2
    assert len(portfolio.history) == 20

    metrics = result.report.metrics
    for key in (
        "annualized_return",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "calmar_ratio",
    ):
        assert key in metrics


def test_data_handler_skips_unavailable_symbols_when_not_strict(tmp_path):
    frames = {"AAA": _make_frame("2022-01-01", 10, 100.0)}
    source = PartiallyMissingSource(frames=frames)
    handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=["AAA", "MISSING"],
            start=datetime(2022, 1, 1),
            end=datetime(2022, 2, 1),
            interval="1d",
            adjust_prices=False,
            cache_dir=tmp_path,
            strict_symbols=False,
        ),
        source=source,
    )
    assert handler.active_symbols == ["AAA"]
