from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def direction(self) -> int:
        if self.quantity > 0:
            return 1
        if self.quantity < 0:
            return -1
        return 0


@dataclass(slots=True)
class ClosedTrade:
    symbol: str
    timestamp: datetime
    quantity: float
    pnl: float
    entry_timestamp: datetime | None = None
    exit_timestamp: datetime | None = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    direction: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
