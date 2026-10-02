"""Portfolio volatility targeting with a hard gross-leverage cap.

A system's published rules define a "native" book (share quantities). The account
holds `k` times that book, where `k = target_vol / estimated native vol` is set at
each close and applied at the next open, and is cut further so gross exposure stays
within `max_gross` times equity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VolTarget:
    target_vol: float = 0.20
    max_gross: float = 3.0
    vol_com_days: float = 60.0
    vol_min_days: int = 60
    annualization: int = 261


class NativeVolEstimator:
    """Zero-mean EWMA of the native book's daily returns."""

    def __init__(self, config: VolTarget) -> None:
        self.config = config
        self.alpha = 1.0 / (1.0 + config.vol_com_days)
        self.var = 0.0
        self.count = 0

    def update(self, daily_return: float) -> None:
        sq = daily_return * daily_return
        self.var = sq if self.count == 0 else (1.0 - self.alpha) * self.var + self.alpha * sq
        self.count += 1

    @property
    def vol(self) -> float:
        return math.sqrt(self.var * self.config.annualization)

    def scale(self) -> float:
        """Multiplier on the native book; zero until the estimate has warmed up."""
        if self.count < self.config.vol_min_days or self.var <= 0.0:
            return 0.0
        return self.config.target_vol / self.vol


def capped_scale(scale: float, native_gross: float, equity: float, max_gross: float) -> float:
    """Largest multiplier <= `scale` that keeps `scale * native_gross` within `max_gross * equity`."""
    if native_gross <= 0.0 or equity <= 0.0:
        return scale if equity > 0.0 else 0.0
    return min(scale, max_gross * equity / native_gross)


def financing_rate(cash: float, rf: float, borrow_spread_bps: float) -> float:
    """Daily rate on the cash balance: the bill rate, plus a spread when borrowing."""
    if cash < 0.0:
        return rf + borrow_spread_bps / 10_000.0 / 252.0
    return rf
