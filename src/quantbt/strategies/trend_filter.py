from __future__ import annotations

from collections import deque
from math import floor

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


class TrendFilteredHoldStrategy(BaseStrategy):
    """
    Paul Tudor Jones's 200-day rule applied to one index (Market Wizards and later
    interviews): be long while the index closes above its 200-day simple moving average of
    closes, be out (in cash) otherwise.

    The signal is read on `signal_symbol` (e.g. SPY) and the position is taken in
    `trade_symbol` (e.g. a 3x SPY fund), using all equity. `sma_window=None` means
    buy-and-hold. Orders are plain market orders, so they fill at the next bar's open.
    """

    def __init__(self, signal_symbol: str, trade_symbol: str, sma_window: int | None = 200) -> None:
        symbols = [signal_symbol] if signal_symbol == trade_symbol else [signal_symbol, trade_symbol]
        super().__init__(symbols=symbols)
        self.signal_symbol = signal_symbol
        self.trade_symbol = trade_symbol
        self.sma_window = sma_window
        self._closes: deque[float] = deque(maxlen=sma_window or 1)
        self.switches = 0

    def _wants_long(self) -> bool | None:
        if self.sma_window is None:
            return True
        if len(self._closes) < self.sma_window:
            return None
        return self._closes[-1] > sum(self._closes) / self.sma_window

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signal_bar = market_event.bars.get(self.signal_symbol)
        trade_bar = market_event.bars.get(self.trade_symbol)
        if signal_bar is None or trade_bar is None or signal_bar.is_stale:
            return []
        self._closes.append(signal_bar.close)

        held = self.portfolio.position_for_symbol(self.trade_symbol).quantity
        want = self._wants_long()
        if want is None:
            return []
        if want and held <= 0:
            quantity = floor(self.portfolio.latest_equity / trade_bar.close)
            if quantity <= 0:
                return []
            self.switches += 1
            return [self.buy(timestamp=market_event.timestamp, symbol=self.trade_symbol, quantity=quantity,
                             metadata={"reason": "above_sma" if self.sma_window else "buy_and_hold"})]
        if not want and held > 0:
            self.switches += 1
            return [self.sell(timestamp=market_event.timestamp, symbol=self.trade_symbol, quantity=int(held),
                              metadata={"reason": "below_sma"})]
        return []
