from __future__ import annotations

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.rs_momentum import RelativeStrengthLeadersStrategy


class FrameSource(DataSource):
    name = "rs_synthetic"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(close: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995, "close": close, "adj_close": close, "volume": 1e6},
        index=index,
    )


def _run(frames, is_member, tmp_path, **kwargs):
    index = next(iter(frames.values())).index
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=list(frames), start=index[0].to_pydatetime(), end=index[-1].to_pydatetime(), cache_dir=tmp_path),
        source=FrameSource(frames),
    )
    strategy = RelativeStrengthLeadersStrategy(symbols=data.active_symbols, is_member=is_member, **kwargs)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0)
    BacktestEngine(
        data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(ExecutionConfig())
    ).run()
    return strategy, portfolio


def _universe(n_days: int = 320):
    index = pd.bdate_range("2020-01-01", periods=n_days)
    t = np.arange(n_days)
    frames = {
        "SPY": _frame(100 * 1.0004**t, index),
        "FAST": _frame(50 * 1.002**t, index),
        "MID": _frame(50 * 1.001**t, index),
        "SLOW": _frame(50 * 0.9995**t, index),
    }
    return index, frames


def test_buys_top_momentum_member_at_next_open(tmp_path):
    index, frames = _universe()
    strategy, portfolio = _run(frames, lambda s, d: True, tmp_path, top_n=1, stop_loss_pct=None)
    held = {s for s, p in portfolio.positions.items() if p.quantity > 0}
    assert held == {"FAST"}
    assert strategy.rebalances > 0


def test_non_members_are_never_bought(tmp_path):
    index, frames = _universe()
    strategy, portfolio = _run(frames, lambda s, d: s != "FAST", tmp_path, top_n=1, stop_loss_pct=None)
    traded = {t.symbol for t in portfolio.closed_trades} | {s for s, p in portfolio.positions.items() if p.quantity}
    assert "FAST" not in traded
    assert "MID" in traded


def test_market_filter_keeps_cash_when_spy_below_200dma(tmp_path):
    index, frames = _universe()
    t = np.arange(len(index))
    frames["SPY"] = _frame(100 * 0.999**t, index)
    strategy, portfolio = _run(frames, lambda s, d: True, tmp_path, top_n=2)
    assert not portfolio.closed_trades
    assert not any(p.quantity for p in portfolio.positions.values())
    assert strategy.filter_off_months == strategy.rebalances


def test_stop_loss_exits_after_eight_percent_drop(tmp_path):
    index, frames = _universe()
    close = frames["FAST"]["close"].to_numpy().copy()
    # Bought at the start of a month around bar ~270; crash 15% a few days later.
    crash_at = 275
    close[crash_at:] *= 0.85
    frames["FAST"] = _frame(close, index)
    strategy, portfolio = _run(frames, lambda s, d: True, tmp_path, top_n=1, market_symbol=None)
    stops = [t for t in portfolio.closed_trades if t.metadata.get("exit_reason") == "STOP_LOSS"]
    assert stops and stops[0].symbol == "FAST"
    assert stops[0].exit_timestamp == index[crash_at + 1]


def test_duplicate_tickers_take_one_slot(tmp_path):
    index, frames = _universe()
    frames["FAST2"] = frames["FAST"].copy()
    strategy, portfolio = _run(frames, lambda s, d: True, tmp_path, top_n=2, stop_loss_pct=None)
    held = {s for s, p in portfolio.positions.items() if p.quantity > 0}
    assert len(held & {"FAST", "FAST2"}) == 1
    assert "MID" in held
