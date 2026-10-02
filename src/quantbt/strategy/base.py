from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent, SignalEvent


class BaseStrategy(ABC):
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.portfolio = None

    def bind_portfolio(self, portfolio: Any) -> None:
        self.portfolio = portfolio

    @abstractmethod
    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        """
        This is the only method strategy authors need to override.
        It receives one bar per symbol and returns zero or more signals.
        """

    def signal(
        self,
        *,
        timestamp: datetime,
        symbol: str,
        side: Side,
        quantity: int | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SignalEvent:
        return SignalEvent(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata or {},
        )

    def buy(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.BUY, **kwargs)

    def sell(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.SELL, **kwargs)

    def buy_moo(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.BUY, order_type=OrderType.MOO, **kwargs)

    def sell_moo(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.SELL, order_type=OrderType.MOO, **kwargs)

    def buy_moc(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.BUY, order_type=OrderType.MOC, **kwargs)

    def sell_moc(self, **kwargs: Any) -> SignalEvent:
        return self.signal(side=Side.SELL, order_type=OrderType.MOC, **kwargs)
