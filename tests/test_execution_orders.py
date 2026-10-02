from __future__ import annotations

from datetime import datetime

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent, OrderEvent
from quantbt.core.models import Bar
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler


def _market_event(ts: datetime, open_: float, high: float, low: float, close: float) -> MarketEvent:
    bar = Bar(
        symbol="AAA",
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        adj_close=close,
    )
    return MarketEvent(timestamp=ts, bars={"AAA": bar})


def test_limit_order_fill():
    execution = SimulatedExecutionHandler(ExecutionConfig(slippage_bps=0.0, spread_bps=0.0))
    execution.submit_order(
        OrderEvent(
            timestamp=datetime(2023, 1, 1),
            symbol="AAA",
            side=Side.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=98.0,
        )
    )

    fills = execution.on_market(_market_event(datetime(2023, 1, 2), open_=100, high=102, low=97, close=101))
    assert len(fills) == 1
    assert fills[0].price == 98.0


def test_bracket_take_profit_oco():
    execution = SimulatedExecutionHandler(ExecutionConfig(slippage_bps=0.0, spread_bps=0.0))
    execution.submit_order(
        OrderEvent(
            timestamp=datetime(2023, 1, 1),
            symbol="AAA",
            side=Side.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
            stop_loss=95.0,
            take_profit=105.0,
        )
    )

    # Entry fill -> creates two protective orders.
    fills_1 = execution.on_market(_market_event(datetime(2023, 1, 2), open_=100, high=101, low=99, close=100))
    assert len(fills_1) == 1
    assert execution.pending_orders == 2

    # Take-profit gets hit and stop-loss sibling is cancelled (OCO).
    fills_2 = execution.on_market(_market_event(datetime(2023, 1, 3), open_=104, high=106, low=103, close=105))
    assert len(fills_2) == 1
    assert fills_2[0].side == Side.SELL
    assert execution.pending_orders == 0

