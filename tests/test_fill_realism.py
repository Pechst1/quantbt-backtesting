"""Synthetic regression tests for order/fill realism fixes in the engine."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent, OrderEvent
from quantbt.core.models import Bar
from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategy.base import BaseStrategy

FREE = ExecutionConfig(
    slippage_bps=0.0,
    spread_bps=0.0,
    commission_pct=0.0,
    exchange_fee_per_share=0.0,
    sec_fee_rate=0.0,
)


class SyntheticSource(DataSource):
    name = "synthetic_fill_realism"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(closes, index=None, highs=None, lows=None, opens=None) -> pd.DataFrame:
    index = index if index is not None else pd.bdate_range("2020-01-01", periods=len(closes))
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": close if opens is None else np.asarray(opens, dtype=float),
            "high": close if highs is None else np.asarray(highs, dtype=float),
            "low": close if lows is None else np.asarray(lows, dtype=float),
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=index,
    )


def _run(frames, strategy, tmp_path, portfolio=None, **engine_kwargs):
    index = sorted(set().union(*[frame.index for frame in frames.values()]))
    config = DataHandlerConfig(symbols=list(frames), start=index[0], end=index[-1], cache_dir=tmp_path)
    data = PublicOHLCVDataHandler(config, source=SyntheticSource(frames))
    portfolio = portfolio or Portfolio(initial_cash=100_000.0, margin_interest_rate=0.0)
    engine = BacktestEngine(
        data_handler=data,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=SimulatedExecutionHandler(FREE),
        **engine_kwargs,
    )
    result = engine.run()
    return engine, portfolio, result


class ScriptedStrategy(BaseStrategy):
    """Emits pre-scripted signals on given bar numbers (1-based)."""

    def __init__(self, symbols, script) -> None:
        super().__init__(symbols)
        self.script = script
        self.bar = 0

    def on_data(self, market_event):
        self.bar += 1
        make = self.script.get(self.bar)
        return make(self, market_event.timestamp) if make else []


def _event(ts, open_, high, low, close) -> MarketEvent:
    bar = Bar("AAA", ts, open_, high, low, close, 1e6, close)
    return MarketEvent(timestamp=ts, bars={"AAA": bar})


def _bracket_entry(stop: float, target: float) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime(2023, 1, 1),
        symbol="AAA",
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        stop_loss=stop,
        take_profit=target,
    )


def test_strategy_exit_cancels_orphaned_bracket(tmp_path):
    script = {
        1: lambda s, ts: [s.buy(timestamp=ts, symbol="A", quantity=10, stop_loss=90, take_profit=130)],
        3: lambda s, ts: [s.sell(timestamp=ts, symbol="A", quantity=10)],
    }
    engine, portfolio, _ = _run(
        {"A": _frame([100, 100, 100, 100, 110, 120, 135, 135])},
        ScriptedStrategy(["A"], script),
        tmp_path,
    )
    assert portfolio.positions["A"].quantity == 0
    assert len(portfolio.closed_trades) == 1
    assert engine.execution_handler.pending_orders == 0


def test_protective_order_cannot_reverse_position_on_same_bar():
    # Strategy exit (market, fills at open) and the old target both trigger on one bar.
    execution = SimulatedExecutionHandler(FREE)
    execution.submit_order(_bracket_entry(stop=90.0, target=105.0))
    execution.on_market(_event(datetime(2023, 1, 2), 100, 101, 99, 100), positions={})
    execution.submit_order(
        OrderEvent(timestamp=datetime(2023, 1, 2), symbol="AAA", side=Side.SELL, quantity=10, order_type=OrderType.MARKET)
    )
    fills = execution.on_market(_event(datetime(2023, 1, 3), 100, 110, 99, 108), positions={"AAA": 10.0})
    assert len(fills) == 1
    assert fills[0].price == 100.0
    assert execution.pending_orders == 0


def test_stop_wins_when_bar_touches_stop_and_target():
    execution = SimulatedExecutionHandler(FREE)
    execution.submit_order(_bracket_entry(stop=95.0, target=105.0))
    execution.on_market(_event(datetime(2023, 1, 2), 100, 101, 99, 100), positions={})
    fills = execution.on_market(_event(datetime(2023, 1, 3), 100, 110, 90, 100), positions={"AAA": 10.0})
    assert len(fills) == 1
    assert fills[0].price == 95.0
    assert execution.pending_orders == 0


def test_target_wins_when_open_gaps_through_it():
    execution = SimulatedExecutionHandler(FREE)
    execution.submit_order(_bracket_entry(stop=95.0, target=105.0))
    execution.on_market(_event(datetime(2023, 1, 2), 100, 101, 99, 100), positions={})
    fills = execution.on_market(_event(datetime(2023, 1, 3), 107, 108, 90, 100), positions={"AAA": 10.0})
    assert len(fills) == 1
    assert fills[0].price == 107.0


def test_stop_can_trigger_on_the_entry_bar():
    execution = SimulatedExecutionHandler(FREE)
    execution.submit_order(_bracket_entry(stop=95.0, target=105.0))
    fills = execution.on_market(_event(datetime(2023, 1, 2), 100, 101, 90, 92), positions={})
    assert [fill.side for fill in fills] == [Side.BUY, Side.SELL]
    assert fills[1].price == 95.0
    assert execution.pending_orders == 0


def test_no_fill_on_forward_filled_bar(tmp_path):
    index_a = pd.bdate_range("2020-01-01", periods=6)
    index_b = index_a.delete(2)  # B has no session on the third date
    script = {2: lambda s, ts: [s.buy(timestamp=ts, symbol="B", quantity=10)]}
    _, portfolio, _ = _run(
        {"A": _frame([1] * 6, index=index_a), "B": _frame([100, 100, 200, 200, 200], index=index_b)},
        ScriptedStrategy(["A", "B"], script),
        tmp_path,
    )
    assert portfolio.positions["B"].quantity == 10
    assert portfolio.positions["B"].avg_price == 200.0


def test_moc_signal_fills_at_next_close_by_default(tmp_path):
    script = {2: lambda s, ts: [s.buy_moc(timestamp=ts, symbol="A", quantity=10)]}
    _, portfolio, _ = _run({"A": _frame([100, 110, 120, 130])}, ScriptedStrategy(["A"], script), tmp_path)
    assert portfolio.positions["A"].avg_price == 120.0


def test_same_bar_moc_fills_available_as_opt_in(tmp_path):
    script = {2: lambda s, ts: [s.buy_moc(timestamp=ts, symbol="A", quantity=10)]}
    _, portfolio, _ = _run(
        {"A": _frame([100, 110, 120, 130])},
        ScriptedStrategy(["A"], script),
        tmp_path,
        same_bar_moc_fills=True,
    )
    assert portfolio.positions["A"].avg_price == 110.0


def test_margin_interest_charged_on_debit_balance():
    portfolio = Portfolio(initial_cash=100_000.0, margin_interest_rate=0.0365)
    portfolio.cash = -100_000.0
    cost = portfolio.apply_financing(timestamp=datetime(2023, 1, 11), previous_timestamp=datetime(2023, 1, 1))
    assert cost == pytest.approx(100.0)
    assert portfolio.cash == pytest.approx(-100_100.0)
    assert portfolio.margin_interest_paid == pytest.approx(100.0)


def test_cash_interest_excludes_short_proceeds():
    portfolio = Portfolio(initial_cash=100_000.0, cash_interest_rate=lambda as_of: 0.0365)
    portfolio.positions["AAA"] = portfolio.position_for_symbol("AAA")
    portfolio.positions["AAA"].quantity = -500
    portfolio.positions["AAA"].avg_price = 100.0
    portfolio.cash = 150_000.0  # 100k equity + 50k short proceeds
    earned = -portfolio.apply_financing(timestamp=datetime(2023, 1, 11), previous_timestamp=datetime(2023, 1, 1))
    assert earned == pytest.approx(100.0)
    assert portfolio.cash_interest_earned == pytest.approx(100.0)


def test_margin_interest_uses_cash_net_of_short_proceeds():
    # 100k equity, 100k short and 150k long: recorded cash is +50k, but net of the
    # short proceeds the account has borrowed 50k.
    portfolio = Portfolio(initial_cash=100_000.0, margin_interest_rate=0.0365, cash_interest_rate=0.0365)
    short = portfolio.position_for_symbol("SSS")
    short.quantity, short.avg_price = -1_000, 100.0
    long = portfolio.position_for_symbol("LLL")
    long.quantity, long.avg_price = 1_500, 100.0
    portfolio.cash = 50_000.0
    cost = portfolio.apply_financing(timestamp=datetime(2023, 1, 11), previous_timestamp=datetime(2023, 1, 1))
    assert cost == pytest.approx(50.0)
    assert portfolio.margin_interest_paid == pytest.approx(50.0)
    assert portfolio.cash_interest_earned == 0.0


def test_leveraged_backtest_pays_financing(tmp_path):
    script = {1: lambda s, ts: [s.buy(timestamp=ts, symbol="A", quantity=1500)]}
    portfolio = Portfolio(initial_cash=100_000.0, leverage=2.0, margin_interest_rate=0.05)
    _, portfolio, result = _run(
        {"A": _frame([100] * 30)}, ScriptedStrategy(["A"], script), tmp_path, portfolio=portfolio
    )
    assert result.margin_interest_paid > 0
    assert portfolio.history[-1].equity < 100_000.0
