from __future__ import annotations

from collections import deque

import numpy as np

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


class SmaCrossStrategy(BaseStrategy):
    def __init__(
        self,
        symbols: list[str],
        short_window: int = 20,
        long_window: int = 100,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
    ) -> None:
        super().__init__(symbols=symbols)
        if short_window >= long_window:
            raise ValueError("short_window must be smaller than long_window.")

        self.short_window = short_window
        self.long_window = long_window
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self._prices = {symbol: deque(maxlen=long_window + 1) for symbol in symbols}
        self._regime = {symbol: 0 for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue

            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < self.long_window:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            fast = float(values[-self.short_window :].mean())
            slow = float(values[-self.long_window :].mean())
            regime = 1 if fast > slow else -1

            prev_regime = self._regime[symbol]
            position = self.portfolio.position_for_symbol(symbol) if self.portfolio else None
            position_qty = position.quantity if position is not None else 0.0

            if regime > 0 and prev_regime <= 0 and position_qty <= 0:
                stop_loss = bar.close * (1.0 - self.stop_loss_pct) if self.stop_loss_pct else None
                take_profit = bar.close * (1.0 + self.take_profit_pct) if self.take_profit_pct else None
                signals.append(
                    self.buy(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
                )
            elif regime < 0 and prev_regime >= 0 and position_qty >= 0:
                stop_loss = bar.close * (1.0 + self.stop_loss_pct) if self.stop_loss_pct else None
                take_profit = bar.close * (1.0 - self.take_profit_pct) if self.take_profit_pct else None
                signals.append(
                    self.sell(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
                )

            self._regime[symbol] = regime

        return signals

