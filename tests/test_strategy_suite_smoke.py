from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategies import (
    AtrBreakoutStrategy,
    BollingerMeanReversionStrategy,
    CciMeanReversionStrategy,
    EmaCrossStrategy,
    MacdSignalStrategy,
    Momentum121Strategy,
    RsiMeanReversionStrategy,
    SmaCrossStrategy,
    StochasticOscillatorStrategy,
    TurtleBreakoutStrategy,
    ZScoreMeanReversionStrategy,
)


class SyntheticSuiteSource(DataSource):
    name = "synthetic_suite"

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


def _make_symbol_frame(index: pd.DatetimeIndex, seed: int, base: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=0.0003, scale=0.012, size=len(index))
    closes = np.empty(len(index), dtype=float)
    closes[0] = base
    for i in range(1, len(index)):
        closes[i] = closes[i - 1] * (1.0 + returns[i])

    opens = closes * (1.0 + rng.normal(0.0, 0.002, size=len(index)))
    highs = np.maximum(opens, closes) * (1.0 + rng.uniform(0.0005, 0.01, size=len(index)))
    lows = np.minimum(opens, closes) * (1.0 - rng.uniform(0.0005, 0.01, size=len(index)))
    volumes = rng.integers(100_000, 1_500_000, size=len(index), endpoint=False).astype(float)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        },
        index=index,
    )


def test_popular_strategies_smoke_on_large_basket(tmp_path):
    symbols = [f"SYM{i:02d}" for i in range(12)]
    index = pd.date_range("2020-01-01", periods=320, freq="D")
    frames = {
        symbol: _make_symbol_frame(index=index, seed=42 + i, base=50.0 + i * 3.0)
        for i, symbol in enumerate(symbols)
    }
    source = SyntheticSuiteSource(frames=frames)

    strategy_builders = [
        ("sma", lambda syms: SmaCrossStrategy(symbols=syms, short_window=20, long_window=100)),
        ("ema", lambda syms: EmaCrossStrategy(symbols=syms, fast_window=12, slow_window=26)),
        ("rsi", lambda syms: RsiMeanReversionStrategy(symbols=syms, rsi_period=14)),
        ("bollinger", lambda syms: BollingerMeanReversionStrategy(symbols=syms)),
        ("macd", lambda syms: MacdSignalStrategy(symbols=syms)),
        ("stoch", lambda syms: StochasticOscillatorStrategy(symbols=syms)),
        ("cci", lambda syms: CciMeanReversionStrategy(symbols=syms)),
        ("momentum", lambda syms: Momentum121Strategy(symbols=syms, lookback=252, skip=21)),
        ("atr", lambda syms: AtrBreakoutStrategy(symbols=syms, atr_period=14, atr_multiplier=1.0)),
        ("turtle", lambda syms: TurtleBreakoutStrategy(symbols=syms, entry_window=55, exit_window=20)),
        ("zscore", lambda syms: ZScoreMeanReversionStrategy(symbols=syms, window=20, entry_z=1.5)),
    ]

    for name, make_strategy in strategy_builders:
        handler = PublicOHLCVDataHandler(
            config=DataHandlerConfig(
                symbols=symbols,
                start=datetime(2020, 1, 1),
                end=datetime(2021, 1, 1),
                interval="1d",
                adjust_prices=False,
                cache_dir=tmp_path / f"cache_{name}",
            ),
            source=source,
        )
        engine = BacktestEngine(
            data_handler=handler,
            strategy=make_strategy(symbols),
            portfolio=Portfolio(
                initial_cash=200_000.0,
                leverage=2.0,
                maintenance_margin_ratio=0.25,
                position_sizer=FixedFractionalSizer(risk_fraction=0.01),
            ),
            execution_handler=SimulatedExecutionHandler(
                config=ExecutionConfig(
                    slippage_bps=1.0,
                    spread_bps=2.0,
                    commission_fixed=0.0,
                    commission_pct=0.0002,
                )
            ),
        )
        result = engine.run(run_name=f"suite_{name}")
        assert len(engine.portfolio.history) == len(index)
        assert "sharpe_ratio" in result.report.metrics
        assert "annualized_return" in result.report.metrics
