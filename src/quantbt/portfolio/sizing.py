from __future__ import annotations

from abc import ABC, abstractmethod
from math import floor

from quantbt.core.events import SignalEvent
from quantbt.core.models import Bar


class BasePositionSizer(ABC):
    @abstractmethod
    def size(self, signal: SignalEvent, portfolio: "Portfolio", bar: Bar) -> int:
        ...


class FixedFractionalSizer(BasePositionSizer):
    def __init__(self, risk_fraction: float = 0.02, max_allocation: float = 0.25) -> None:
        self.risk_fraction = max(risk_fraction, 0.0)
        self.max_allocation = max(max_allocation, 0.0)

    def size(self, signal: SignalEvent, portfolio: "Portfolio", bar: Bar) -> int:
        allocation = min(self.risk_fraction, self.max_allocation)
        capital = portfolio.latest_equity * allocation
        if bar.close <= 0:
            return 0
        return max(floor(capital / bar.close), 0)


class KellyCriterionSizer(BasePositionSizer):
    def __init__(
        self,
        win_probability: float = 0.55,
        win_loss_ratio: float = 1.5,
        fraction: float = 0.5,
        max_allocation: float = 0.3,
    ) -> None:
        self.win_probability = win_probability
        self.win_loss_ratio = win_loss_ratio
        self.fraction = fraction
        self.max_allocation = max_allocation

    def size(self, signal: SignalEvent, portfolio: "Portfolio", bar: Bar) -> int:
        if self.win_loss_ratio <= 0.0 or bar.close <= 0:
            return 0
        kelly = self.win_probability - ((1.0 - self.win_probability) / self.win_loss_ratio)
        kelly = max(min(kelly * self.fraction, self.max_allocation), 0.0)
        capital = portfolio.latest_equity * kelly
        return max(floor(capital / bar.close), 0)


class RiskBasedSizer(BasePositionSizer):
    def __init__(self, risk_per_trade: float = 0.01, fallback_allocation: float = 0.05) -> None:
        self.risk_per_trade = max(risk_per_trade, 0.0)
        self.fallback_allocation = max(fallback_allocation, 0.0)

    def size(self, signal: SignalEvent, portfolio: "Portfolio", bar: Bar) -> int:
        if bar.close <= 0:
            return 0

        risk_budget = portfolio.latest_equity * self.risk_per_trade
        stop = signal.stop_loss if signal.stop_loss is not None else signal.stop_price
        if stop is not None:
            risk_per_unit = abs(bar.close - stop)
            if risk_per_unit > 0:
                return max(floor(risk_budget / risk_per_unit), 0)

        fallback_capital = portfolio.latest_equity * self.fallback_allocation
        return max(floor(fallback_capital / bar.close), 0)

