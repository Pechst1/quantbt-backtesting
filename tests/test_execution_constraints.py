from __future__ import annotations

from datetime import datetime

from quantbt.altdata.csv_sources import StaticBorrowDataSource
from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent, OrderEvent
from quantbt.core.models import Bar
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler


def _event(ts: datetime, close: float = 10.0, volume: float = 5000.0) -> MarketEvent:
    bar = Bar(
        symbol="AAA",
        timestamp=ts,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        adj_close=close,
    )
    return MarketEvent(timestamp=ts, bars={"AAA": bar})


def test_short_order_rejected_when_hard_to_borrow():
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(reject_hard_to_borrow=True),
        borrow_source=StaticBorrowDataSource(annualized_fee=0.2, hard_to_borrow_symbols={"AAA"}),
    )
    execution.submit_order(
        OrderEvent(
            timestamp=datetime(2024, 1, 2),
            symbol="AAA",
            side=Side.SELL,
            quantity=100,
            order_type=OrderType.MARKET,
            metadata={"short_sale": True},
        )
    )
    fills = execution.on_market(_event(datetime(2024, 1, 3)))
    assert fills == []
    assert execution.rejected_orders == 1


def test_moc_order_fills_at_close():
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(slippage_bps=0.0, spread_bps=0.0, volume_impact_bps=0.0),
    )
    order = OrderEvent(
        timestamp=datetime(2024, 1, 2),
        symbol="AAA",
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MOC,
    )
    fill = execution.execute_immediate(order, _event(datetime(2024, 1, 2), close=12.5))
    assert fill is not None
    assert fill.price == 12.5

