from __future__ import annotations

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.wizards import CookBreadthTimingStrategy, OkumusDeepValueStrategy, ShortTermReversalStrategy


class FrameSource(DataSource):
    name = "wizards_synthetic"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(close: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995, "close": close, "adj_close": close, "volume": 1e6},
        index=index,
    )


def _run(frames, strategy_cls, tmp_path, leverage=1.0, **kwargs):
    index = next(iter(frames.values())).index
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=list(frames), start=index[0].to_pydatetime(), end=index[-1].to_pydatetime(), cache_dir=tmp_path),
        source=FrameSource(frames),
    )
    strategy = strategy_cls(symbols=data.active_symbols, **kwargs)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=leverage)
    BacktestEngine(
        data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(ExecutionConfig())
    ).run()
    return strategy, portfolio


def test_okumus_buys_only_after_60pct_drop_and_sells_at_target(tmp_path):
    n = 600
    index = pd.bdate_range("2010-01-01", periods=n)
    crash = np.concatenate([np.full(260, 100.0), np.linspace(100, 35, 40), np.linspace(35, 60, 300)])
    mild = np.concatenate([np.full(260, 100.0), np.linspace(100, 55, 40), np.full(300, 55.0)])
    frames = {"SPY": _frame(np.full(n, 100.0), index), "CRASH": _frame(crash, index), "MILD": _frame(mild, index)}
    strategy, portfolio = _run(frames, OkumusDeepValueStrategy, tmp_path, is_member=lambda s, d: True, max_positions=2)
    traded = {t.symbol for t in portfolio.closed_trades} | {s for s, p in portfolio.positions.items() if p.quantity}
    assert traded == {"CRASH"}
    trade = portfolio.closed_trades[0]
    assert trade.entry_price <= 40.5
    assert trade.metadata.get("exit_reason") == "TARGET"
    assert trade.exit_price >= 1.5 * trade.entry_price * 0.99


def test_okumus_time_stop(tmp_path):
    n = 900
    index = pd.bdate_range("2010-01-01", periods=n)
    crash = np.concatenate([np.full(260, 100.0), np.linspace(100, 30, 40), np.full(600, 30.0)])
    frames = {"SPY": _frame(np.full(n, 100.0), index), "CRASH": _frame(crash, index)}
    _, portfolio = _run(frames, OkumusDeepValueStrategy, tmp_path, is_member=lambda s, d: True, time_stop_bars=100)
    assert portfolio.closed_trades[0].metadata.get("exit_reason") == "TIME_STOP"


def test_reversal_holds_worst_performer_and_rotates(tmp_path):
    n = 60
    index = pd.bdate_range("2020-01-06", periods=n)
    rng = np.random.default_rng(0)
    frames = {"SPY": _frame(np.full(n, 100.0), index)}
    for k in range(10):
        frames[f"S{k}"] = _frame(100 * np.cumprod(1 + rng.normal(0, 0.02, n)), index)
    signal_dates = {ts.date() for ts in index.to_series().groupby(index.to_period("W-FRI")).max()}
    strategy, portfolio = _run(
        frames, ShortTermReversalStrategy, tmp_path, is_member=lambda s, d: True, signal_dates=signal_dates, lookback=5
    )
    assert strategy.rebalances >= 10
    assert "SPY" not in {t.symbol for t in portfolio.closed_trades}
    # One name per week (10% of 10); every closed trade lasted about a week.
    assert all((t.exit_timestamp - t.entry_timestamp).days <= 14 for t in portfolio.closed_trades)


def test_cook_buys_spy_after_extreme_selling_breadth(tmp_path):
    n = 700
    index = pd.bdate_range("2010-01-01", periods=n)
    rng = np.random.default_rng(1)
    common = rng.normal(0, 0.01, n)
    common[650:665] = -0.03  # a broad, persistent sell-off
    frames = {"SPY": _frame(100 * np.cumprod(1 + common), index)}
    for k in range(60):
        frames[f"S{k}"] = _frame(100 * np.cumprod(1 + common + rng.normal(0, 0.01, n)), index)
    strategy, portfolio = _run(
        frames, CookBreadthTimingStrategy, tmp_path, is_member=lambda s, d: True, mode="cash", min_history=300
    )
    assert strategy.buy_signals >= 1
    states = pd.DataFrame(strategy.breadth_log, columns=["date", "b", "c", "state"]).set_index("date")
    assert (states.loc[index[655] :, "state"] == "buy").any()
    held = {t.symbol for t in portfolio.closed_trades} | {s for s, p in portfolio.positions.items() if p.quantity}
    assert held <= {"SPY"}
