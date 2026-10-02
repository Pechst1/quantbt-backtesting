from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from quantbt.core.enums import EventType, OrderType, Side
from quantbt.core.models import Bar


@dataclass(slots=True)
class MarketEvent:
    timestamp: datetime
    bars: dict[str, Bar]
    event_type: EventType = field(default=EventType.MARKET, init=False)


@dataclass(slots=True)
class SignalEvent:
    timestamp: datetime
    symbol: str
    side: Side
    quantity: int | None = None
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    signal_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: EventType = field(default=EventType.SIGNAL, init=False)


@dataclass(slots=True)
class OrderEvent:
    timestamp: datetime
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    oco_group: str | None = None
    order_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: EventType = field(default=EventType.ORDER, init=False)


@dataclass(slots=True)
class FillEvent:
    timestamp: datetime
    symbol: str
    side: Side
    quantity: int
    price: float
    commission: float
    slippage_cost: float
    order_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: EventType = field(default=EventType.FILL, init=False)

