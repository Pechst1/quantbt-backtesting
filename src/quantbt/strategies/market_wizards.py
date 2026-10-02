from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from math import floor

import numpy as np
import pandas as pd

from quantbt.altdata.base import (
    CatalystDataSource,
    EarningsDataSource,
    FundamentalsDataSource,
    FuturesCurveDataSource,
    MacroDataSource,
    MacroSurpriseDataSource,
    VolatilityDataSource,
)
from quantbt.altdata.csv_sources import CSVSecurityMasterDataSource
from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


RATE_YIELD_INSTRUMENTS = {"2YY=F", "5YY=F", "10Y=F", "30Y=F", "^FVX", "^TNX", "^TYX"}


def _sma(values: np.ndarray, period: int) -> float | None:
    if len(values) < period:
        return None
    return float(np.mean(values[-period:]))


def _std(values: np.ndarray, period: int) -> float | None:
    if len(values) < period:
        return None
    return float(np.std(values[-period:], ddof=0))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = np.empty(period, dtype=float)
    recent_highs = highs[-(period + 1) :]
    recent_lows = lows[-(period + 1) :]
    recent_closes = closes[-(period + 1) :]
    for idx in range(1, period + 1):
        trs[idx - 1] = max(
            recent_highs[idx] - recent_lows[idx],
            abs(recent_highs[idx] - recent_closes[idx - 1]),
            abs(recent_lows[idx] - recent_closes[idx - 1]),
        )
    return float(np.mean(trs))


def _risk_size(
    *,
    equity: float,
    price: float,
    risk_per_unit: float,
    risk_pct: float,
    max_notional_pct: float,
) -> int:
    if price <= 0 or risk_per_unit <= 0 or equity <= 0:
        return 0
    risk_budget = equity * risk_pct
    risk_qty = floor(risk_budget / risk_per_unit)
    notional_qty = floor((equity * max_notional_pct) / price)
    return max(min(risk_qty, max(notional_qty, 0)), 0)


def _weight_target_size(*, equity: float, price: float, target_weight: float) -> int:
    if equity <= 0 or price <= 0 or target_weight == 0:
        return 0
    quantity = floor(abs(target_weight) * equity / price)
    if quantity <= 0:
        return 0
    return quantity if target_weight > 0 else -quantity


def _percentile_map(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    ordered = sorted(scores.items(), key=lambda item: item[1])
    denom = max(len(ordered) - 1, 1)
    out: dict[str, float] = {}
    for rank, (symbol, _) in enumerate(ordered):
        out[symbol] = 100.0 * rank / denom
    return out


def _safe_return(values: np.ndarray, lookback: int, *, exclude_current: bool = False) -> float | None:
    needed = lookback + 1 + (1 if exclude_current else 0)
    if len(values) < needed:
        return None
    arr = values[:-1] if exclude_current else values
    start = float(arr[-lookback - 1])
    end = float(arr[-1])
    if start <= 0:
        return None
    return end / start - 1.0


@dataclass(slots=True)
class PairSpec:
    left: str
    right: str

    @property
    def key(self) -> str:
        return f"{self.left}|{self.right}"


@dataclass(slots=True)
class MacroTargetCandidate:
    symbol: str
    desired_qty: int
    price: float
    score: float
    region: str
    asset_class: str
    theme: str
    reason: str


@dataclass(slots=True)
class MacroDirectionPlan:
    symbol: str
    sign: int
    score: float
    region: str
    theme: str
    asset_class: str
    reason: str
    entry_threshold: float
    exit_threshold: float
    risk_pct: float
    max_notional_pct: float
    carry: bool = False


@dataclass(slots=True)
class BarbellTargetPlan:
    symbol: str
    engine: str
    target_weight: float
    score: float
    region: str
    theme: str
    asset_class: str
    reason: str
    core_target_weight: float = 0.0
    overlay_target_weight: float = 0.0
    convex_target_weight: float = 0.0
    hedge_target_weight: float = 0.0
    trend_sleeve_target_weight: float = 0.0
    rates_curve_target_weight: float = 0.0
    curve_rv_target_weight: float = 0.0
    commodity_futures_target_weight: float = 0.0
    crisis_trend_target_weight: float = 0.0
    dollar_squeeze_target_weight: float = 0.0
    reversal_target_weight: float = 0.0
    melt_up_target_weight: float = 0.0
    overlay_reason: str = ""
    convex_reason: str = ""
    hedge_reason: str = ""
    trend_sleeve_reason: str = ""
    rates_curve_reason: str = ""
    curve_rv_reason: str = ""
    commodity_futures_reason: str = ""
    crisis_trend_reason: str = ""
    dollar_squeeze_reason: str = ""
    reversal_reason: str = ""
    melt_up_reason: str = ""
    leader_score: float = 0.0
    lag_score: float = 0.0
    overlay_threshold: float = 0.0
    coherence: float = 0.0
    velocity_signal: float = 0.0
    velocity_multiplier: float = 1.0
    macro_surprise_score: float = 0.0
    macro_surprise_coherence: float = 0.0
    macro_surprise_confirmed: bool = False
    core_phase: int = 0
    overlay_phase: int = 0
    core_phase_multiplier: float = 1.0
    overlay_phase_multiplier: float = 1.0
    reallocation_target_weight: float = 0.0
    extreme_regime: bool = False
    cross_sleeve_amplifier: bool = False
    cross_sleeve_alignment_count: int = 0


@dataclass(slots=True)
class BarbellTargetCandidate:
    symbol: str
    engine: str
    desired_qty: int
    price: float
    score: float
    region: str
    asset_class: str
    theme: str
    reason: str


class TrendBreakoutWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: trend-following breakout with ATR sizing and pyramiding.
    """

    def __init__(
        self,
        symbols: list[str],
        breakout_lookback: int = 55,
        exit_lookback: int = 20,
        trend_ma: int = 100,
        atr_period: int = 20,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.25,
        max_units: int = 4,
        pyramid_atr_step: float = 0.5,
        trailing_atr_mult: float = 2.5,
    ) -> None:
        super().__init__(symbols=symbols)
        maxlen = max(breakout_lookback, exit_lookback, trend_ma, atr_period) + 5
        self.breakout_lookback = breakout_lookback
        self.exit_lookback = exit_lookback
        self.trend_ma = trend_ma
        self.atr_period = atr_period
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        self.max_units = max_units
        self.pyramid_atr_step = pyramid_atr_step
        self.trailing_atr_mult = trailing_atr_mult
        self._highs = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}
        self._unit_qty: dict[str, int] = {}
        self._last_add_price: dict[str, float] = {}
        self._highest_close: dict[str, float] = {}
        self._lowest_close: dict[str, float] = {}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty > 0:
                self._highest_close[symbol] = max(self._highest_close.get(symbol, bar.close), bar.close)
            elif qty < 0:
                current = self._lowest_close.get(symbol, bar.close)
                self._lowest_close[symbol] = min(current, bar.close)
            if qty == 0:
                self._unit_qty.pop(symbol, None)
                self._last_add_price.pop(symbol, None)
                self._highest_close.pop(symbol, None)
                self._lowest_close.pop(symbol, None)
            elif prev <= 0 < qty or prev >= 0 > qty:
                self._unit_qty.setdefault(symbol, max(int(abs(qty)), 1))
                self._last_add_price[symbol] = bar.close
                self._highest_close[symbol] = bar.close
                self._lowest_close[symbol] = bar.close
            elif qty != prev and qty * prev > 0:
                self._last_add_price[symbol] = bar.close
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            if len(closes) <= max(self.breakout_lookback, self.exit_lookback, self.trend_ma, self.atr_period):
                continue

            atr = _atr(highs, lows, closes, self.atr_period)
            trend = _sma(closes, self.trend_ma)
            if atr is None or trend is None:
                continue

            prev_break_high = float(np.max(highs[-self.breakout_lookback - 1 : -1]))
            prev_break_low = float(np.min(lows[-self.breakout_lookback - 1 : -1]))
            prev_exit_high = float(np.max(highs[-self.exit_lookback - 1 : -1]))
            prev_exit_low = float(np.min(lows[-self.exit_lookback - 1 : -1]))
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)

            if qty > 0:
                trail = self._highest_close.get(symbol, bar.close) - self.trailing_atr_mult * atr
                if bar.close < prev_exit_low or bar.close <= trail or bar.close < trend:
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "TREND_BREAKOUT", "reason": "TREND_EXIT"},
                        )
                    )
                    continue
            elif qty < 0:
                trail = self._lowest_close.get(symbol, bar.close) + self.trailing_atr_mult * atr
                if bar.close > prev_exit_high or bar.close >= trail or bar.close > trend:
                    signals.append(
                        self.buy_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "TREND_BREAKOUT", "reason": "TREND_EXIT"},
                        )
                    )
                    continue

            unit_qty = _risk_size(
                equity=self.portfolio.latest_equity,
                price=bar.close,
                risk_per_unit=max(atr, 1e-9),
                risk_pct=self.risk_pct,
                max_notional_pct=self.max_notional_pct,
            )
            if unit_qty <= 0:
                continue

            if qty == 0:
                if bar.close > prev_break_high and bar.close > trend:
                    self._unit_qty[symbol] = unit_qty
                    signals.append(
                        self.buy_moo(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=unit_qty,
                            metadata={"strategy": "TREND_BREAKOUT", "direction": 1},
                        )
                    )
                elif bar.close < prev_break_low and bar.close < trend:
                    self._unit_qty[symbol] = unit_qty
                    signals.append(
                        self.sell_moo(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=unit_qty,
                            metadata={
                                "strategy": "TREND_BREAKOUT",
                                "direction": -1,
                                "short_sale": True,
                            },
                        )
                    )
                continue

            base_unit = max(self._unit_qty.get(symbol, unit_qty), 1)
            current_units = max(int(abs(qty) / base_unit), 1)
            last_add = self._last_add_price.get(symbol, bar.close)
            if qty > 0 and current_units < self.max_units and bar.close >= last_add + self.pyramid_atr_step * atr:
                signals.append(
                    self.buy_moo(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=base_unit,
                        metadata={"strategy": "TREND_BREAKOUT", "direction": 1},
                    )
                )
            elif qty < 0 and current_units < self.max_units and bar.close <= last_add - self.pyramid_atr_step * atr:
                signals.append(
                    self.sell_moo(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=base_unit,
                        metadata={
                            "strategy": "TREND_BREAKOUT",
                            "direction": -1,
                            "short_sale": True,
                        },
                    )
                )

        return signals


class TrendPullbackReversalWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: trade pullbacks within a 200-day regime using z-score extremes.
    """

    def __init__(
        self,
        symbols: list[str],
        regime_ma: int = 200,
        z_window: int = 20,
        z_entry: float = 1.5,
        mean_reversion_ma: int = 20,
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.20,
    ) -> None:
        super().__init__(symbols=symbols)
        maxlen = regime_ma + 5
        self.regime_ma = regime_ma
        self.z_window = z_window
        self.z_entry = z_entry
        self.mean_reversion_ma = mean_reversion_ma
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        self._highs = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._pending_direction = {symbol: 0 for symbol in symbols}
        self._entry_price: dict[str, float] = {}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty == 0:
                self._entry_price.pop(symbol, None)
            elif prev <= 0 < qty or prev >= 0 > qty:
                self._entry_price[symbol] = bar.close
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            if len(closes) < max(self.regime_ma, self.z_window, self.mean_reversion_ma, self.atr_period) + 1:
                continue

            regime = _sma(closes, self.regime_ma)
            revert_ma = _sma(closes, self.mean_reversion_ma)
            z_std = _std(closes, self.z_window)
            atr = _atr(highs, lows, closes, self.atr_period)
            if regime is None or revert_ma is None or z_std is None or atr is None or z_std <= 0:
                continue
            zscore = (bar.close - float(np.mean(closes[-self.z_window :]))) / z_std
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev_close = float(closes[-2])

            if qty > 0:
                stop = self._entry_price.get(symbol, bar.close) - self.stop_atr_mult * atr
                if bar.close >= revert_ma or bar.close < regime or bar.close <= stop:
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "TREND_PULLBACK", "reason": "MEAN_REVERSION_EXIT"},
                        )
                    )
            elif qty < 0:
                stop = self._entry_price.get(symbol, bar.close) + self.stop_atr_mult * atr
                if bar.close <= revert_ma or bar.close > regime or bar.close >= stop:
                    signals.append(
                        self.buy_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "TREND_PULLBACK", "reason": "MEAN_REVERSION_EXIT"},
                        )
                    )

            if qty != 0:
                self._pending_direction[symbol] = 0
                continue

            risk_qty = _risk_size(
                equity=self.portfolio.latest_equity,
                price=bar.close,
                risk_per_unit=max(atr, 1e-9),
                risk_pct=self.risk_pct,
                max_notional_pct=self.max_notional_pct,
            )
            if risk_qty <= 0:
                continue

            pending = self._pending_direction[symbol]
            if pending > 0 and bar.close > prev_close and bar.close > regime:
                signals.append(
                    self.buy_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=risk_qty,
                        metadata={"strategy": "TREND_PULLBACK", "reason": "CONFIRM_UP"},
                    )
                )
                self._pending_direction[symbol] = 0
            elif pending < 0 and bar.close < prev_close and bar.close < regime:
                signals.append(
                    self.sell_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=risk_qty,
                        metadata={
                            "strategy": "TREND_PULLBACK",
                            "reason": "CONFIRM_DOWN",
                            "short_sale": True,
                        },
                    )
                )
                self._pending_direction[symbol] = 0
            elif pending != 0:
                self._pending_direction[symbol] = 0

            if bar.close > regime and zscore <= -self.z_entry:
                self._pending_direction[symbol] = 1
            elif bar.close < regime and zscore >= self.z_entry:
                self._pending_direction[symbol] = -1

        return signals


class StockLeadershipBreakoutWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: buy high relative-strength leaders breaking out of proper bases.
    """

    def __init__(
        self,
        symbols: list[str],
        fundamentals_source: FundamentalsDataSource,
        security_master: CSVSecurityMasterDataSource,
        rs_lookback: int = 63,
        base_window: int = 50,
        volume_multiplier: float = 1.5,
        min_rs_percentile: float = 85.0,
        min_industry_percentile: float = 75.0,
        stop_pct: float = 0.08,
        max_base_range: float = 0.35,
        max_extension_pct: float = 0.05,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.20,
    ) -> None:
        super().__init__(symbols=symbols)
        maxlen = max(rs_lookback, base_window) + 5
        self.fundamentals_source = fundamentals_source
        self.security_master = security_master
        self.rs_lookback = rs_lookback
        self.base_window = base_window
        self.volume_multiplier = volume_multiplier
        self.min_rs_percentile = min_rs_percentile
        self.min_industry_percentile = min_industry_percentile
        self.stop_pct = stop_pct
        self.max_base_range = max_base_range
        self.max_extension_pct = max_extension_pct
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._volumes = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._base_high: dict[str, float] = {}
        self._entry_price: dict[str, float] = {}
        self._planned_base_high: dict[str, float] = {}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty == 0:
                self._base_high.pop(symbol, None)
                self._entry_price.pop(symbol, None)
            elif prev <= 0 < qty:
                self._base_high[symbol] = self._planned_base_high.pop(symbol, bar.close)
                self._entry_price[symbol] = bar.close
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        as_of = market_event.timestamp.date()
        industry_map = self.security_master.industry_map(as_of)

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._closes[symbol].append(bar.close)
            self._volumes[symbol].append(bar.volume)

        symbol_returns: dict[str, float] = {}
        for symbol in self.symbols:
            closes = np.fromiter(self._closes[symbol], dtype=float)
            value = _safe_return(closes, self.rs_lookback)
            if value is not None:
                symbol_returns[symbol] = value
        rs_pct = _percentile_map(symbol_returns)

        industry_scores: dict[str, list[float]] = {}
        for symbol, score in symbol_returns.items():
            industry = industry_map.get(symbol, "UNSPECIFIED")
            industry_scores.setdefault(industry, []).append(score)
        industry_avg = {name: float(np.mean(values)) for name, values in industry_scores.items()}
        industry_pct = _percentile_map(industry_avg)

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            closes = np.fromiter(self._closes[symbol], dtype=float)
            volumes = np.fromiter(self._volumes[symbol], dtype=float)
            if len(closes) < self.base_window + 1 or len(volumes) < self.base_window + 1:
                continue

            fundamentals = self.fundamentals_source.snapshot(symbol, as_of)
            if fundamentals is None:
                continue
            eps_growth = fundamentals.eps_growth_yoy or 0.0
            rev_growth = fundamentals.revenue_growth_yoy or 0.0
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            current_rs = rs_pct.get(symbol, 0.0)
            current_industry_pct = industry_pct.get(industry_map.get(symbol, "UNSPECIFIED"), 0.0)
            base_slice = closes[-self.base_window - 1 : -1]
            base_high = float(np.max(base_slice))
            base_low = float(np.min(base_slice))
            base_range = base_high / max(base_low, 1e-9) - 1.0
            avg_volume = float(np.mean(volumes[-self.base_window - 1 : -1]))

            if qty > 0:
                stop_level = self._entry_price.get(symbol, bar.close) * (1.0 - self.stop_pct)
                breakout_level = self._base_high.get(symbol, base_high)
                if (
                    bar.close < breakout_level
                    or bar.close <= stop_level
                    or current_rs < self.min_rs_percentile - 15.0
                    or eps_growth <= 0
                    or rev_growth <= 0
                ):
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "LEADER_BREAKOUT", "reason": "LEADERSHIP_EXIT"},
                        )
                    )
                continue

            if (
                current_rs < self.min_rs_percentile
                or current_industry_pct < self.min_industry_percentile
                or eps_growth <= 0
                or rev_growth <= 0
                or base_range > self.max_base_range
                or bar.volume <= self.volume_multiplier * max(avg_volume, 1.0)
                or bar.close <= base_high
                or bar.close > base_high * (1.0 + self.max_extension_pct)
            ):
                continue

            quantity = _risk_size(
                equity=self.portfolio.latest_equity,
                price=bar.close,
                risk_per_unit=max(bar.close * self.stop_pct, 1e-9),
                risk_pct=self.risk_pct,
                max_notional_pct=self.max_notional_pct,
            )
            if quantity <= 0:
                continue

            self._planned_base_high[symbol] = base_high
            signals.append(
                self.buy_moo(
                    timestamp=market_event.timestamp,
                    symbol=symbol,
                    quantity=quantity,
                    metadata={"strategy": "LEADER_BREAKOUT"},
                )
            )

        return signals


class EventMomentumWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: trade earnings surprises only when the pre-event tape was not already euphoric.
    """

    def __init__(
        self,
        symbols: list[str],
        earnings_source: EarningsDataSource,
        benchmark_symbol: str,
        surprise_threshold: float = 1.5,
        pre_event_lookback: int = 20,
        hold_bars: int = 15,
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.15,
    ) -> None:
        super().__init__(symbols=symbols)
        maxlen = max(pre_event_lookback, atr_period, hold_bars) + 10
        self.earnings_source = earnings_source
        self.benchmark_symbol = benchmark_symbol.upper()
        self.surprise_threshold = surprise_threshold
        self.pre_event_lookback = pre_event_lookback
        self.hold_bars = hold_bars
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        tracked = set(symbols) | {self.benchmark_symbol}
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in tracked}
        self._highs = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._entry_price: dict[str, float] = {}
        self._holding_bars = {symbol: 0 for symbol in symbols}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty == 0:
                self._entry_price.pop(symbol, None)
                self._holding_bars[symbol] = 0
            elif prev <= 0 < qty or prev >= 0 > qty:
                self._entry_price[symbol] = bar.close
                self._holding_bars[symbol] = 0
            else:
                self._holding_bars[symbol] += 1
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        for symbol, bar in market_event.bars.items():
            if symbol in self._closes:
                self._closes[symbol].append(bar.close)
            if symbol in self._highs:
                self._highs[symbol].append(bar.high)
                self._lows[symbol].append(bar.low)

        benchmark_closes = np.fromiter(self._closes[self.benchmark_symbol], dtype=float)
        today = market_event.timestamp.date()

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            closes = np.fromiter(self._closes[symbol], dtype=float)
            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            atr = _atr(highs, lows, closes, self.atr_period) if len(closes) > self.atr_period else None
            short_ma = _sma(closes, 5)

            if qty > 0 and atr is not None:
                stop = self._entry_price.get(symbol, bar.close) - self.stop_atr_mult * atr
                if self._holding_bars[symbol] >= self.hold_bars or (short_ma is not None and bar.close < short_ma) or bar.close <= stop:
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "EVENT_MOMENTUM", "reason": "POST_EVENT_EXIT"},
                        )
                    )
            elif qty < 0 and atr is not None:
                stop = self._entry_price.get(symbol, bar.close) + self.stop_atr_mult * atr
                if self._holding_bars[symbol] >= self.hold_bars or (short_ma is not None and bar.close > short_ma) or bar.close >= stop:
                    signals.append(
                        self.buy_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "EVENT_MOMENTUM", "reason": "POST_EVENT_EXIT"},
                        )
                    )

            if qty != 0 or atr is None:
                continue

            stock_rel = _safe_return(closes, self.pre_event_lookback, exclude_current=True)
            bench_rel = _safe_return(benchmark_closes, self.pre_event_lookback, exclude_current=True)
            if stock_rel is None or bench_rel is None:
                continue
            pre_event_relative = stock_rel - bench_rel

            for event in self.earnings_source.announcements_on(today):
                if event.symbol.upper() != symbol:
                    continue
                if event.estimate_std <= 0:
                    continue
                quantity = _risk_size(
                    equity=self.portfolio.latest_equity,
                    price=bar.close,
                    risk_per_unit=max(atr, 1e-9),
                    risk_pct=self.risk_pct,
                    max_notional_pct=self.max_notional_pct,
                )
                if quantity <= 0:
                    continue

                if event.sue >= self.surprise_threshold and pre_event_relative <= 0.0:
                    signals.append(
                        self.buy_moo(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=quantity,
                            metadata={"strategy": "EVENT_MOMENTUM", "sue": event.sue},
                        )
                    )
                elif event.sue <= -self.surprise_threshold and pre_event_relative >= 0.0:
                    signals.append(
                        self.sell_moo(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=quantity,
                            metadata={
                                "strategy": "EVENT_MOMENTUM",
                                "sue": event.sue,
                                "short_sale": True,
                            },
                        )
                    )

        return signals


class ValueCatalystWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: cheap/expensive valuation alone is insufficient, catalyst timing matters.
    """

    def __init__(
        self,
        symbols: list[str],
        fundamentals_source: FundamentalsDataSource,
        catalyst_source: CatalystDataSource,
        stop_pct: float = 0.10,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.20,
        min_asset_backing: float = 0.30,
    ) -> None:
        super().__init__(symbols=symbols)
        self.fundamentals_source = fundamentals_source
        self.catalyst_source = catalyst_source
        self.stop_pct = stop_pct
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        self.min_asset_backing = min_asset_backing
        self._entry_price: dict[str, float] = {}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty == 0:
                self._entry_price.pop(symbol, None)
            elif prev <= 0 < qty or prev >= 0 > qty:
                self._entry_price[symbol] = bar.close
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        as_of = market_event.timestamp.date()
        snapshots = {
            symbol: self.fundamentals_source.snapshot(symbol, as_of)
            for symbol in self.symbols
        }
        pe_values = [snap.pe_ratio for snap in snapshots.values() if snap is not None and snap.pe_ratio is not None]
        pb_values = [snap.pb_ratio for snap in snapshots.values() if snap is not None and snap.pb_ratio is not None]
        fcf_values = [snap.fcf_yield for snap in snapshots.values() if snap is not None and snap.fcf_yield is not None]
        if not pe_values or not pb_values or not fcf_values:
            return signals

        pe_lo, pe_hi = float(np.quantile(pe_values, 0.4)), float(np.quantile(pe_values, 0.6))
        pb_lo, pb_hi = float(np.quantile(pb_values, 0.4)), float(np.quantile(pb_values, 0.6))
        fcf_lo, fcf_hi = float(np.quantile(fcf_values, 0.4)), float(np.quantile(fcf_values, 0.6))

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            snapshot = snapshots.get(symbol)
            if bar is None or snapshot is None:
                continue
            active = self.catalyst_source.active_events(symbol, as_of)
            positive = [event for event in active if event.direction > 0]
            negative = [event for event in active if event.direction < 0]
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            asset_backing = max(snapshot.cash_per_share or 0.0, snapshot.book_value_per_share or 0.0) / max(bar.close, 1e-9)
            cheap = (
                (snapshot.pe_ratio is not None and snapshot.pe_ratio <= pe_lo)
                or (snapshot.pb_ratio is not None and snapshot.pb_ratio <= pb_lo)
                or (snapshot.fcf_yield is not None and snapshot.fcf_yield >= fcf_hi)
            )
            expensive = (
                (snapshot.pe_ratio is not None and snapshot.pe_ratio >= pe_hi)
                or (snapshot.pb_ratio is not None and snapshot.pb_ratio >= pb_hi)
                or (snapshot.fcf_yield is not None and snapshot.fcf_yield <= fcf_lo)
            )

            if qty > 0:
                normalized = (
                    snapshot.pe_ratio is not None
                    and snapshot.pb_ratio is not None
                    and snapshot.pe_ratio >= pe_hi
                    and snapshot.pb_ratio >= pb_hi
                )
                stop = self._entry_price.get(symbol, bar.close) * (1.0 - self.stop_pct)
                target_hit = any(event.target_price is not None and bar.close >= event.target_price for event in positive)
                if not positive or normalized or bar.close <= stop or target_hit:
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "VALUE_CATALYST", "reason": "THESIS_RESOLVED"},
                        )
                    )
                continue
            if qty < 0:
                normalized = (
                    snapshot.pe_ratio is not None
                    and snapshot.pb_ratio is not None
                    and snapshot.pe_ratio <= pe_lo
                    and snapshot.pb_ratio <= pb_lo
                )
                stop = self._entry_price.get(symbol, bar.close) * (1.0 + self.stop_pct)
                target_hit = any(event.target_price is not None and bar.close <= event.target_price for event in negative)
                if not negative or normalized or bar.close >= stop or target_hit:
                    signals.append(
                        self.buy_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "VALUE_CATALYST", "reason": "THESIS_RESOLVED"},
                        )
                    )
                continue

            quantity = _risk_size(
                equity=self.portfolio.latest_equity,
                price=bar.close,
                risk_per_unit=max(bar.close * self.stop_pct, 1e-9),
                risk_pct=self.risk_pct,
                max_notional_pct=self.max_notional_pct,
            )
            if quantity <= 0:
                continue
            if positive and cheap and asset_backing >= self.min_asset_backing:
                signals.append(
                    self.buy_moo(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=quantity,
                        metadata={"strategy": "VALUE_CATALYST"},
                    )
                )
            elif negative and expensive:
                signals.append(
                    self.sell_moo(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=quantity,
                        metadata={"strategy": "VALUE_CATALYST", "short_sale": True},
                    )
                )

        return signals


class TopDownGlobalMacroWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: point-in-time macro themes expressed through liquid macro proxies.
    """

    def __init__(
        self,
        symbols: list[str],
        macro_source: MacroDataSource,
        security_master: CSVSecurityMasterDataSource,
        atr_period: int = 20,
        trend_ma: int = 200,
        z_window: int = 36,
        z_window_max: int = 72,
        min_macro_observations: int = 24,
        enter_threshold: float = 0.50,
        exit_threshold: float = 0.20,
        trailing_atr_mult: float = 3.0,
        risk_pct: float = 0.0075,
        relative_risk_pct: float = 0.00375,
        max_notional_pct: float = 0.15,
        carry_risk_pct: float = 0.0050,
        carry_max_notional_pct: float = 0.10,
        carry_exit_threshold: float = 0.40,
        trend_penalty: float = 0.50,
        conviction_power: float = 2.0,
        max_gross_total: float = 2.5,
        max_net_total: float = 1.2,
        max_gross_per_asset_class: float = 0.30,
        max_gross_per_region: float = 0.40,
        max_theme_expressions: int = 3,
        stale_after_days: int = 45,
        stale_half_life_days: int = 30,
        adaptive_weight_lookback: int = 60,
        adaptive_weight_min_obs: int = 36,
        use_adaptive_weights: bool = False,
        macro_inputs_are_zscores: bool = False,
    ) -> None:
        super().__init__(symbols=symbols)
        self.macro_source = macro_source
        self.security_master = security_master
        self.atr_period = atr_period
        self.trend_ma = trend_ma
        self.z_window = z_window
        self.z_window_max = max(z_window_max, z_window)
        self.min_macro_observations = min_macro_observations
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.trailing_atr_mult = trailing_atr_mult
        self.risk_pct = risk_pct
        self.relative_risk_pct = relative_risk_pct
        self.max_notional_pct = max_notional_pct
        self.carry_risk_pct = carry_risk_pct
        self.carry_max_notional_pct = carry_max_notional_pct
        self.carry_exit_threshold = carry_exit_threshold
        self.trend_penalty = trend_penalty
        self.conviction_power = conviction_power
        self.max_gross_total = max_gross_total
        self.max_net_total = max_net_total
        self.max_gross_per_asset_class = max_gross_per_asset_class
        self.max_gross_per_region = max_gross_per_region
        self.max_theme_expressions = max_theme_expressions
        self.stale_after_days = stale_after_days
        self.stale_half_life_days = stale_half_life_days
        self.adaptive_weight_lookback = adaptive_weight_lookback
        self.adaptive_weight_min_obs = adaptive_weight_min_obs
        self.macro_inputs_are_zscores = bool(macro_inputs_are_zscores)
        maxlen = max(trend_ma + 5, atr_period + 5, 1_600)
        self._highs = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._dates = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._high_water: dict[str, float] = {}
        self._low_water: dict[str, float] = {}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}
        self._direction_plans: dict[str, MacroDirectionPlan] = {}
        self._last_monthly_refresh: tuple[int, int] | None = None
        self._last_weekly_resize: tuple[int, int, int] | None = None
        self._region_macro_state_cache: dict[tuple[str, date], dict[str, object]] = {}

    def _needs_monthly_refresh(self, as_of: date) -> bool:
        marker = (as_of.year, as_of.month)
        if self._last_monthly_refresh is None:
            self._last_monthly_refresh = marker
            return True
        changed = marker != self._last_monthly_refresh
        if changed:
            self._last_monthly_refresh = marker
        return changed

    def _needs_weekly_resize(self, as_of: date) -> bool:
        marker = (as_of.year, as_of.month, as_of.isocalendar().week)
        if self._last_weekly_resize is None:
            self._last_weekly_resize = marker
            return True
        changed = marker != self._last_weekly_resize
        if changed:
            self._last_weekly_resize = marker
        return changed

    def _needs_rebalance(self, as_of: date) -> bool:
        return self._needs_monthly_refresh(as_of)

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty > 0:
                if prev <= 0:
                    self._high_water[symbol] = bar.high
                else:
                    self._high_water[symbol] = max(self._high_water.get(symbol, bar.high), bar.high)
                self._low_water.pop(symbol, None)
            elif qty < 0:
                if prev >= 0:
                    self._low_water[symbol] = bar.low
                else:
                    self._low_water[symbol] = min(self._low_water.get(symbol, bar.low), bar.low)
                self._high_water.pop(symbol, None)
            else:
                self._high_water.pop(symbol, None)
                self._low_water.pop(symbol, None)
            self._prev_qty[symbol] = qty

    def _exposure_template(self, *, asset_class: str, sector: str) -> tuple[float, float, float, float, float]:
        asset_class = asset_class.upper().strip()
        sector = sector.upper().strip()
        if asset_class in {"BOND", "DURATION", "RATES_FUTURE"}:
            return (-0.6, -1.0, -1.0, 0.3, -0.4)
        if asset_class in {"INFLATION_LINKED_BOND", "TIP"}:
            return (0.2, 0.8, -0.4, 0.3, 0.0)
        if asset_class == "GOLD":
            return (-0.1, 0.9, -0.6, 0.2, -0.1)
        if asset_class in {"ENERGY", "COMMODITY", "COMMODITY_FUTURE"}:
            return (0.4, 0.9, -0.2, 0.2, 0.2)
        if asset_class == "FX":
            return (0.5, -0.4, 0.7, -0.3, 0.4)
        if asset_class == "CREDIT":
            return (0.7, -0.5, -0.6, 0.5, 0.9)
        if asset_class == "DEFENSIVE":
            return (-0.2, 0.0, 0.2, 0.2, -0.1)
        if sector == "ENERGY":
            return (0.4, 0.8, -0.2, 0.2, 0.2)
        if sector == "UTILITIES":
            return (-0.2, 0.1, 0.3, 0.2, -0.1)
        return (0.8, -0.2, -0.5, 0.7, 0.6)

    def _thresholds_for_symbol(self, *, symbol: str, asset_class: str) -> tuple[float, float]:
        symbol = symbol.upper()
        asset_class = asset_class.upper().strip()
        if symbol in {"EFA", "GLD", "TIP"}:
            return (0.60, 0.25)
        if asset_class == "CREDIT":
            return (0.30, 0.15)
        if symbol in {"DBC", "USO", "CL=F", "GC=F", "HG=F", "ZC=F"} or asset_class in {
            "COMMODITY",
            "COMMODITY_FUTURE",
            "ENERGY",
        }:
            return (0.40, 0.20)
        if asset_class in {"EQUITY_INDEX", "EQUITY"}:
            return (0.45, 0.20)
        if asset_class in {"BOND", "DURATION", "RATES_FUTURE", "INFLATION_LINKED_BOND", "TIP"}:
            return (0.45, 0.20)
        if asset_class == "FX" or symbol == "DX=F":
            return (0.40, 0.20)
        return (self.enter_threshold, self.exit_threshold)

    def _trend_period_for_symbol(self, *, symbol: str, asset_class: str, sign: int = 0) -> int:
        symbol = symbol.upper()
        asset_class = asset_class.upper().strip()
        if getattr(self, "enable_asymmetric_trend_filter_layer", False) and sign < 0:
            return 50
        if getattr(self, "enable_asymmetric_trend_filter_layer", False):
            if asset_class in {"BOND", "DURATION", "RATES_FUTURE", "INFLATION_LINKED_BOND", "TIP", "CREDIT"}:
                return 100
            if asset_class in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY", "GOLD"} or symbol in {
                "GLD",
                "USO",
                "DBC",
                "CL=F",
                "GC=F",
                "HG=F",
                "ZC=F",
            }:
                return 150
            if asset_class == "FX" or symbol in {"UUP", "DX=F"}:
                return 100
            return max(self.trend_ma, 200)
        if asset_class in {"BOND", "DURATION", "RATES_FUTURE", "INFLATION_LINKED_BOND", "TIP", "CREDIT"}:
            return 50
        if asset_class in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY", "GOLD", "FX"} or symbol in {
            "GLD",
            "USO",
            "DBC",
            "CL=F",
            "GC=F",
            "HG=F",
            "ZC=F",
            "UUP",
            "DX=F",
        }:
            return 100
        return max(self.trend_ma, 200)

    def _risk_budget_for_symbol(
        self,
        *,
        symbol: str,
        asset_class: str,
        carry: bool,
    ) -> tuple[float, float]:
        symbol = symbol.upper()
        asset_class = asset_class.upper().strip()
        if carry:
            return (self.carry_risk_pct, self.carry_max_notional_pct)
        if symbol in {"EFA", "GLD", "TIP"}:
            return (self.risk_pct * 0.70, min(self.max_notional_pct, 0.08))
        if asset_class == "CREDIT":
            return (self.risk_pct * 1.05, max(self.max_notional_pct, 0.18))
        if asset_class in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY"} or symbol in {
            "DBC",
            "USO",
            "CL=F",
            "GC=F",
            "HG=F",
            "ZC=F",
        }:
            return (self.risk_pct, max(self.max_notional_pct, 0.18))
        if asset_class in {"BOND", "DURATION", "INFLATION_LINKED_BOND", "TIP"}:
            return (self.risk_pct * 0.90, max(self.max_notional_pct, 0.14))
        if asset_class == "FX":
            return (self.relative_risk_pct, min(self.max_notional_pct, 0.10))
        return (self.risk_pct, self.max_notional_pct)

    def _macro_factor_frame(self, history: list[object]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "growth": [snap.growth_surprise for snap in history],
                "inflation": [snap.inflation_surprise for snap in history],
                "policy": [snap.policy_surprise for snap in history],
                "liquidity": [snap.liquidity_surprise for snap in history],
                "credit": [snap.credit_surprise for snap in history],
            },
            index=pd.to_datetime([snap.as_of for snap in history]),
        )

    def _normalize_macro_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()

        available_prior = max(len(frame) - 1, 1)
        min_obs = max(self.min_macro_observations, min(self.z_window, available_prior))
        max_obs = max(min_obs, self.z_window_max)
        normalized = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
        for column in frame.columns:
            values = frame[column].to_numpy(dtype=float)
            zscores = np.full(len(values), np.nan, dtype=float)
            for idx in range(len(values)):
                prior = values[max(0, idx - max_obs) : idx]
                if len(prior) < min_obs:
                    continue
                mean = float(np.mean(prior))
                rolling_std = float(np.std(prior, ddof=0))
                std_series = pd.Series(prior).rolling(min_obs, min_periods=min_obs).std(ddof=0).dropna()
                long_term_median_std = float(std_series.median()) if not std_series.empty else rolling_std
                std_used = max(rolling_std, long_term_median_std * 0.5, 1e-6)
                zscores[idx] = float((values[idx] - mean) / std_used)
            normalized[column] = zscores
        return normalized

    def _velocity_metrics(
        self,
        current_z: dict[str, float],
        previous_z: dict[str, float],
    ) -> tuple[dict[str, float], float, float]:
        deltas = {
            name: float(current_z[name] - previous_z[name])
            for name in current_z
        }
        values = np.array(list(deltas.values()), dtype=float)
        magnitude = float(np.sqrt(np.sum(values**2)))
        alignment = float(abs(float(np.sum(values))) / (float(np.sum(np.abs(values))) + 1e-9))
        return deltas, magnitude * alignment, alignment

    def _region_macro_state(
        self,
        region: str,
        as_of: date,
    ) -> dict[str, object] | None:
        latest = self.macro_source.snapshot(region, as_of)
        if latest is None:
            return None
        cache_key = (region.upper(), latest.cache_key)
        cached = self._region_macro_state_cache.get(cache_key)
        if cached is not None:
            return cached
        history = self.macro_source.history(region, as_of, lookback=self.z_window_max + 1)
        if len(history) < self.min_macro_observations + 1:
            return None
        frame = self._macro_factor_frame(history)
        normalized = frame.astype(float).copy() if self.macro_inputs_are_zscores else self._normalize_macro_frame(frame)
        current_row = normalized.iloc[-1]
        if current_row.isna().any():
            return None
        current_z = {name: float(value) for name, value in current_row.items()}
        previous_z = {name: 0.0 for name in current_z}
        velocity = {name: 0.0 for name in current_z}
        velocity_signal = 0.0
        velocity_alignment = 0.0
        if len(normalized) >= 2:
            previous_row = normalized.iloc[-2]
            if not previous_row.isna().any():
                previous_z = {name: float(value) for name, value in previous_row.items()}
                velocity, velocity_signal, velocity_alignment = self._velocity_metrics(current_z, previous_z)
        state = {
            "zscores": current_z,
            "previous_zscores": previous_z,
            "velocity": velocity,
            "velocity_signal": float(velocity_signal),
            "velocity_alignment": float(velocity_alignment),
            "snapshot": history[-1],
        }
        self._region_macro_state_cache[cache_key] = state
        return state

    def _region_directional_score(self, zscores: dict[str, float]) -> float:
        return float(
            0.35 * zscores["growth"]
            - 0.25 * zscores["inflation"]
            - 0.15 * zscores["policy"]
            + 0.15 * zscores["liquidity"]
            + 0.10 * zscores["credit"]
        )

    def _region_zscores(
        self,
        region: str,
        as_of: date,
    ) -> tuple[dict[str, float], object] | None:
        state = self._region_macro_state(region, as_of)
        if state is None:
            return None
        return (
            dict(state["zscores"]),  # type: ignore[arg-type]
            state["snapshot"],
        )

    def _classify_theme(self, zscores: dict[str, float]) -> str:
        growth = zscores["growth"]
        inflation = zscores["inflation"]
        liquidity = zscores["liquidity"]
        if growth <= -0.5 and inflation >= 0.5:
            return "STAGFLATION"
        if growth >= 0.5 and inflation >= 0.25:
            return "REFLATION"
        if growth <= -0.5 and inflation <= 0.25:
            return "DISINFLATION"
        if growth >= 0.25 and inflation <= 0.25 and liquidity >= 0.0:
            return "GOLDILOCKS"
        return "MIXED"

    def _theme_expression_sign(
        self,
        *,
        symbol: str,
        asset_class: str,
        theme: str,
    ) -> int:
        sym = symbol.upper()
        asset = asset_class.upper()
        equity = {"SPY", "EFA", "EEM", "EWJ"}
        rates = {"TLT", "IEF", "ZT=F", "ZF=F", "ZN=F", "ZB=F"}
        credit = {"HYG", "LQD", "EMB"}
        inflation = {"TIP"}
        gold = {"GLD", "GC=F"}
        commodities = {"USO", "DBC", "CL=F", "HG=F", "ZC=F"}
        fx = {"UUP", "DX=F"}

        if theme == "REFLATION":
            if sym in equity or sym in commodities or sym in credit or sym in gold or sym in inflation:
                return 1
            if sym in rates or sym in fx or sym == "LQD":
                return -1
        elif theme == "DISINFLATION":
            if sym in rates or sym in gold or sym in {"LQD", "TIP", "UUP"}:
                return 1
            if sym in {"SPY", "EFA", "EEM", "EWJ", "USO", "DBC", "CL=F", "HG=F", "HYG", "EMB"}:
                return -1
        elif theme == "STAGFLATION":
            if sym in gold or sym in inflation or sym in commodities or sym in fx:
                return 1
            if sym in {"TLT", "IEF", "HYG", "SPY", "EFA", "EEM", "EWJ", "LQD", "EMB"}:
                return -1
        elif theme == "GOLDILOCKS":
            if sym in equity or sym in credit:
                return 1
            if sym in {"TLT", "IEF", "ZT=F", "ZF=F", "ZN=F", "ZB=F", "UUP", "DX=F", "GLD"}:
                return -1
        elif theme == "MIXED":
            if sym in credit:
                return 1
            if sym in {"SPY", "EFA", "EEM", "EWJ", "UUP"}:
                return 0
            if sym in commodities or sym in gold or sym in inflation or sym in rates:
                return 0

        if asset in {"EQUITY_INDEX", "EQUITY"} and theme in {"REFLATION", "GOLDILOCKS"}:
            return 1
        if asset in {"EQUITY_INDEX", "EQUITY"} and theme in {"DISINFLATION", "STAGFLATION"}:
            return -1
        if asset in {"BOND", "DURATION"} and theme == "DISINFLATION":
            return 1
        if asset in {"BOND", "DURATION"} and theme in {"REFLATION", "STAGFLATION"}:
            return -1
        return 0

    def _relative_overlays(
        self,
        region_states: dict[str, dict[str, float]],
        region_themes: dict[str, str],
    ) -> dict[str, float]:
        overlays = {symbol: 0.0 for symbol in self.symbols}
        if "US" not in region_states:
            return overlays

        def apply_pair(relative_score: float, long_symbol: str, short_symbol: str) -> None:
            overlays[long_symbol] = overlays.get(long_symbol, 0.0) + relative_score
            overlays[short_symbol] = overlays.get(short_symbol, 0.0) - relative_score
            overlays["UUP"] = overlays.get("UUP", 0.0) + 0.35 * relative_score

        us = region_states["US"]
        for region, symbol in (("EUROPE", "EFA"), ("JAPAN", "EWJ"), ("EM", "EEM")):
            peer = region_states.get(region)
            if peer is None:
                continue
            relative_score = 0.5 * (us["growth"] - peer["growth"]) + 0.3 * (us["policy"] - peer["policy"])
            relative_score += 0.2 * (us["credit"] - peer["credit"])
            multiplier = 0.75 if "MIXED" in {region_themes.get("US"), region_themes.get(region)} else 0.50
            apply_pair(multiplier * relative_score, "SPY", symbol)
        return overlays

    def _monthly_returns(self, symbol: str) -> pd.Series:
        closes = list(self._closes[symbol])
        dates = list(self._dates[symbol])
        if len(closes) < 40 or len(closes) != len(dates):
            return pd.Series(dtype=float)
        series = pd.Series(closes, index=pd.to_datetime(dates))
        month_end = series.groupby(series.index.to_period("M")).last()
        returns = month_end.pct_change().dropna()
        returns.index = returns.index.to_timestamp()
        return returns

    def _adaptive_weights(
        self,
        *,
        symbol: str,
        region: str,
        template: tuple[float, float, float, float, float],
        as_of: date,
    ) -> tuple[float, float, float, float, float]:
        macro_history = self.macro_source.history(
            region,
            as_of,
            lookback=self.adaptive_weight_lookback + self.z_window_max,
        )
        if len(macro_history) < self.adaptive_weight_min_obs + self.z_window:
            return template

        frame = self._macro_factor_frame(macro_history)
        zframe = self._normalize_macro_frame(frame).dropna()
        returns = self._monthly_returns(symbol)
        if returns.empty:
            return template
        aligned = zframe.join(returns.rename("ret"), how="inner").tail(self.adaptive_weight_lookback)
        if len(aligned) < self.adaptive_weight_min_obs:
            return template

        x = aligned[["growth", "inflation", "policy", "liquidity", "credit"]].to_numpy(dtype=float)
        y = aligned["ret"].to_numpy(dtype=float)
        x = np.column_stack([np.ones(len(x)), x])
        coeffs, *_ = np.linalg.lstsq(x, y, rcond=None)
        betas = coeffs[1:]
        if betas.shape != (5,) or not np.all(np.isfinite(betas)):
            return template
        beta_norm = float(np.sum(np.abs(betas)))
        template_arr = np.array(template, dtype=float)
        template_norm = float(np.sum(np.abs(template_arr)))
        if beta_norm <= 1e-6 or template_norm <= 1e-6:
            return template
        scaled_betas = betas / beta_norm * template_norm
        blended = 0.5 * template_arr + 0.5 * scaled_betas
        return tuple(float(beta) for beta in blended)

    def _instrument_multiplier(self, symbol: str) -> float:
        return 1.0

    def _instrument_notional(self, symbol: str, quantity: float, price: float) -> float:
        return float(quantity) * float(price) * self._instrument_multiplier(symbol)

    def _projected_exposure_ok(
        self,
        *,
        projected_positions: dict[str, int],
        prices: dict[str, float],
        region_map: dict[str, str],
        asset_map: dict[str, str],
        equity: float,
        max_gross_total: float | None = None,
        max_net_total: float | None = None,
        max_gross_per_asset_class: float | None = None,
        max_gross_per_region: float | None = None,
    ) -> bool:
        if equity <= 0:
            return False
        gross_limit = self.max_gross_total if max_gross_total is None else float(max_gross_total)
        net_limit = self.max_net_total if max_net_total is None else float(max_net_total)
        asset_limit = (
            self.max_gross_per_asset_class
            if max_gross_per_asset_class is None
            else float(max_gross_per_asset_class)
        )
        region_limit = self.max_gross_per_region if max_gross_per_region is None else float(max_gross_per_region)
        gross_total = 0.0
        net_total = 0.0
        gross_by_region: dict[str, float] = {}
        gross_by_asset: dict[str, float] = {}
        for symbol, qty in projected_positions.items():
            price = prices.get(symbol)
            if price is None or qty == 0:
                continue
            exposure = self._instrument_notional(symbol, float(qty), price)
            gross = abs(exposure)
            gross_total += gross
            net_total += exposure
            region = region_map.get(symbol, "GLOBAL")
            asset = asset_map.get(symbol, "UNSPECIFIED")
            gross_by_region[region] = gross_by_region.get(region, 0.0) + gross
            gross_by_asset[asset] = gross_by_asset.get(asset, 0.0) + gross

        if gross_total / equity > gross_limit + 1e-9:
            return False
        if abs(net_total) / equity > net_limit + 1e-9:
            return False
        if any(value / equity > region_limit + 1e-9 for value in gross_by_region.values()):
            return False
        if any(value / equity > asset_limit + 1e-9 for value in gross_by_asset.values()):
            return False
        return True

    def _fit_target_qty(
        self,
        *,
        symbol: str,
        desired_qty: int,
        projected_positions: dict[str, int],
        prices: dict[str, float],
        region_map: dict[str, str],
        asset_map: dict[str, str],
        equity: float,
        convex_override: bool = False,
    ) -> int:
        current_qty = int(projected_positions.get(symbol, 0))
        if current_qty == desired_qty:
            return current_qty
        if current_qty == 0 or np.sign(current_qty) == np.sign(desired_qty):
            sign = 1 if desired_qty > 0 else -1
            low = abs(current_qty)
            high = abs(desired_qty)
            best = abs(current_qty)
            while low <= high:
                mid = (low + high) // 2
                trial = dict(projected_positions)
                trial[symbol] = sign * mid
                if self._projected_exposure_ok(
                    projected_positions=trial,
                    prices=prices,
                    region_map=region_map,
                    asset_map=asset_map,
                    equity=equity,
                    max_gross_per_asset_class=(
                        max(self.max_gross_per_asset_class, 1.0) if convex_override else None
                    ),
                    max_gross_per_region=max(self.max_gross_per_region, 0.85) if convex_override else None,
                ):
                    best = mid
                    low = mid + 1
                else:
                    high = mid - 1
            return sign * best
        return 0

    def _emit_target_delta(
        self,
        *,
        market_event: MarketEvent,
        symbol: str,
        current_qty: int,
        desired_qty: int,
        score: float,
        reason: str,
    ) -> SignalEvent | None:
        delta = desired_qty - current_qty
        if delta == 0:
            return None
        metadata = {
            "strategy": "GLOBAL_MACRO",
            "reason": reason,
            "score": score,
            "target_qty": desired_qty,
        }
        if delta > 0:
            return self.buy_moo(
                timestamp=market_event.timestamp,
                symbol=symbol,
                quantity=int(delta),
                metadata=metadata,
            )
        metadata["short_sale"] = desired_qty < 0
        return self.sell_moo(
            timestamp=market_event.timestamp,
            symbol=symbol,
            quantity=int(abs(delta)),
            metadata=metadata,
        )

    def _conviction_scale(
        self,
        *,
        score_mag: float,
        entry_threshold: float,
        exit_threshold: float,
    ) -> float:
        if score_mag < exit_threshold:
            return 0.0
        denom = max(entry_threshold - exit_threshold, 1e-9)
        conviction = min(max((score_mag - exit_threshold) / denom, 0.0), 1.0)
        return float(conviction**self.conviction_power)

    def _trend_multiplier(
        self,
        *,
        sign: int,
        close: float,
        trend: float | None,
        atr_value: float | None = None,
        asset_class: str | None = None,
    ) -> float:
        if sign == 0 or trend is None:
            return 1.0
        if sign > 0 and close >= trend:
            return 1.0
        if sign < 0 and close <= trend:
            return 1.0
        if getattr(self, "enable_asymmetric_trend_filter_layer", False) and atr_value is not None and atr_value > 1e-9:
            distance_atr = (close - trend) / max(atr_value, 1e-9)
            severity = min(abs(distance_atr) / 2.0, 1.0)
            return float(1.0 - 0.40 * severity)
        return self.trend_penalty

    def _refresh_direction_plans(
        self,
        *,
        as_of: date,
        sector_map: dict[str, str],
        region_map: dict[str, str],
        asset_map: dict[str, str],
    ) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
        unique_regions = {region_map.get(symbol, "GLOBAL") for symbol in self.symbols}
        region_states: dict[str, dict[str, float]] = {}
        region_snapshots: dict[str, object] = {}
        region_themes: dict[str, str] = {}
        region_directional_scores: dict[str, float] = {}
        for region in unique_regions:
            state = self._region_zscores(region, as_of)
            if state is None:
                continue
            zscores, snapshot = state
            region_states[region] = zscores
            region_snapshots[region] = snapshot
            region_themes[region] = self._classify_theme(zscores)
            region_directional_scores[region] = self._region_directional_score(zscores)

        relative_overlays = self._relative_overlays(region_states, region_themes)
        max_region_abs = max((abs(score) for score in region_directional_scores.values()), default=0.0)
        carry_mode = bool(region_directional_scores) and max_region_abs < self.carry_exit_threshold
        plans: dict[str, MacroDirectionPlan] = {}

        for symbol in self.symbols:
            region = region_map.get(symbol, "GLOBAL")
            asset_class = asset_map.get(symbol, "EQUITY")
            entry_threshold, exit_threshold = self._thresholds_for_symbol(symbol=symbol, asset_class=asset_class)
            carry_budget = self._risk_budget_for_symbol(symbol=symbol, asset_class=asset_class, carry=True)
            base_budget = self._risk_budget_for_symbol(symbol=symbol, asset_class=asset_class, carry=False)
            if region not in region_states:
                plans[symbol] = MacroDirectionPlan(
                    symbol=symbol,
                    sign=0,
                    score=0.0,
                    region=region,
                    theme="MIXED",
                    asset_class=asset_class,
                    reason="NO_MACRO",
                    entry_threshold=entry_threshold,
                    exit_threshold=exit_threshold,
                    risk_pct=base_budget[0],
                    max_notional_pct=base_budget[1],
                )
                continue

            zscores = region_states[region]
            snapshot = region_snapshots[region]
            theme = region_themes.get(region, "MIXED")
            template = self._exposure_template(
                asset_class=asset_class,
                sector=sector_map.get(symbol, "UNSPECIFIED"),
            )
            weights = self._adaptive_weights(symbol=symbol, region=region, template=template, as_of=as_of)
            factor_vector = np.array(
                [
                    zscores["growth"],
                    zscores["inflation"],
                    zscores["policy"],
                    zscores["liquidity"],
                    zscores["credit"],
                ],
                dtype=float,
            )
            raw_score = float(np.dot(np.array(weights, dtype=float), factor_vector))
            raw_score = float(np.clip(raw_score, -3.0, 3.0))
            age_days = (as_of - snapshot.release_date).days
            if age_days > self.stale_after_days:
                plans[symbol] = MacroDirectionPlan(
                    symbol=symbol,
                    sign=0,
                    score=0.0,
                    region=region,
                    theme=theme,
                    asset_class=asset_class,
                    reason="STALE_MACRO",
                    entry_threshold=entry_threshold,
                    exit_threshold=exit_threshold,
                    risk_pct=base_budget[0],
                    max_notional_pct=base_budget[1],
                )
                continue
            raw_score *= float(np.clip(getattr(snapshot, "quality_score", 1.0), 0.0, 1.0))
            if age_days > self.stale_half_life_days:
                raw_score *= 0.5

            theme_sign = self._theme_expression_sign(symbol=symbol, asset_class=asset_class, theme=theme)
            relative_overlay = float(np.clip(relative_overlays.get(symbol, 0.0), -2.0, 2.0))
            region_direction = region_directional_scores.get(region, 0.0)
            carry_strength = max(self.carry_exit_threshold - abs(region_direction), 0.0)

            sign = 0
            signed_score = 0.0
            reason = "NEUTRAL"
            carry = False

            if carry_mode and symbol.upper() in {"HYG", "LQD"}:
                sign = 1
                signed_score = max(exit_threshold + carry_strength, exit_threshold)
                reason = "CARRY_LAYER"
                carry = True
            elif theme == "MIXED":
                if symbol.upper() in {"HYG", "LQD"} and carry_strength > 0.0:
                    sign = 1
                    signed_score = max(exit_threshold + 0.75 * carry_strength + 0.25 * max(raw_score, 0.0), exit_threshold)
                    reason = "MIXED_CARRY"
                    carry = True
                elif symbol.upper() in {"SPY", "EFA", "EEM", "EWJ", "UUP"} and abs(relative_overlay) >= exit_threshold * 0.5:
                    sign = 1 if relative_overlay > 0 else -1
                    signed_score = relative_overlay
                    reason = "MIXED_RELATIVE"
            else:
                aligned_score = theme_sign * raw_score if theme_sign != 0 else 0.0
                combined_support = aligned_score + abs(relative_overlay)
                if theme_sign == 0:
                    if abs(relative_overlay) >= exit_threshold:
                        sign = 1 if relative_overlay > 0 else -1
                        signed_score = relative_overlay
                        reason = "RELATIVE"
                elif combined_support >= exit_threshold:
                    sign = theme_sign
                    signed_score = sign * combined_support
                    reason = "DIRECTIONAL"
                    if abs(relative_overlay) > max(aligned_score, 0.0) and relative_overlay != 0.0:
                        sign = 1 if relative_overlay > 0 else -1
                        signed_score = sign * max(abs(relative_overlay), combined_support)
                        reason = "RELATIVE_DOMINANT"

            risk_pct, max_notional_pct = carry_budget if carry else base_budget
            signed_score = float(np.clip(signed_score, -3.0, 3.0))
            plans[symbol] = MacroDirectionPlan(
                symbol=symbol,
                sign=sign,
                score=signed_score,
                region=region,
                theme=theme,
                asset_class=asset_class,
                reason=reason,
                entry_threshold=entry_threshold,
                exit_threshold=exit_threshold,
                risk_pct=risk_pct,
                max_notional_pct=max_notional_pct,
                carry=carry,
            )

        self._direction_plans = plans
        return region_states, region_themes

    def _target_quantity_from_plan(
        self,
        *,
        symbol: str,
        plan: MacroDirectionPlan,
        bar: object,
        atr: float,
        closes: np.ndarray,
    ) -> int:
        if self.portfolio is None or plan.sign == 0:
            return 0
        trend_period = self._trend_period_for_symbol(symbol=symbol, asset_class=plan.asset_class)
        trend = _sma(closes, trend_period)
        trend_multiplier = self._trend_multiplier(sign=plan.sign, close=bar.close, trend=trend)
        conviction = self._conviction_scale(
            score_mag=abs(plan.score),
            entry_threshold=plan.entry_threshold,
            exit_threshold=plan.exit_threshold,
        )
        if conviction <= 0.0:
            return 0
        full_qty = _risk_size(
            equity=self.portfolio.latest_equity,
            price=bar.close,
            risk_per_unit=max(atr, 1e-9),
            risk_pct=plan.risk_pct,
            max_notional_pct=plan.max_notional_pct,
        )
        scaled_qty = int(round(full_qty * conviction * trend_multiplier))
        return plan.sign * max(scaled_qty, 0)

    def _passes_resize_hysteresis(self, *, current_qty: int, desired_qty: int) -> bool:
        if current_qty == desired_qty:
            return False
        if current_qty == 0 or desired_qty == 0 or np.sign(current_qty) != np.sign(desired_qty):
            return True
        delta = abs(desired_qty - current_qty)
        if delta < 3:
            return False
        return (delta / max(abs(current_qty), 1)) >= 0.10

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        as_of = market_event.timestamp.date()
        sector_map = self.security_master.sector_map(as_of)
        region_map = self.security_master.region_map(as_of)
        asset_map = self.security_master.asset_class_map(as_of)

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            self._dates[symbol].append(as_of)
            if hasattr(self, "_research_closes") and symbol in self._research_closes:
                self._research_closes[symbol].append(bar.close)
                self._research_dates[symbol].append(as_of)

        self._sync_position_state(market_event)

        blocked_symbols: set[str] = set()
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            atr = _atr(highs, lows, closes, self.atr_period)
            if atr is None:
                continue
            qty = int(self.portfolio.position_for_symbol(symbol).quantity)
            if qty > 0:
                stop_level = self._high_water.get(symbol, bar.high) - self.trailing_atr_mult * atr
                if bar.close < stop_level:
                    blocked_symbols.add(symbol)
                    signal = self._emit_target_delta(
                        market_event=market_event,
                        symbol=symbol,
                        current_qty=qty,
                        desired_qty=0,
                        score=0.0,
                        reason="TRAILING_STOP",
                    )
                    if signal is not None:
                        signals.append(signal)
            elif qty < 0:
                stop_level = self._low_water.get(symbol, bar.low) + self.trailing_atr_mult * atr
                if bar.close > stop_level:
                    blocked_symbols.add(symbol)
                    signal = self._emit_target_delta(
                        market_event=market_event,
                        symbol=symbol,
                        current_qty=qty,
                        desired_qty=0,
                        score=0.0,
                        reason="TRAILING_STOP",
                    )
                    if signal is not None:
                        signals.append(signal)

        monthly_refresh = self._needs_monthly_refresh(as_of)
        weekly_resize = self._needs_weekly_resize(as_of) or monthly_refresh
        if monthly_refresh:
            _, region_themes = self._refresh_direction_plans(
                as_of=as_of,
                sector_map=sector_map,
                region_map=region_map,
                asset_map=asset_map,
            )
        else:
            region_themes = {
                plan.region: plan.theme
                for plan in self._direction_plans.values()
                if plan.sign != 0
            }
        if not weekly_resize:
            return signals

        prices = {
            symbol: market_event.bars[symbol].close
            for symbol in self.symbols
            if symbol in market_event.bars
        }
        equity = max(float(self.portfolio.latest_equity), 1e-9)
        projected_positions = {
            symbol: int(self.portfolio.position_for_symbol(symbol).quantity)
            for symbol in self.symbols
        }
        active_theme_symbols: dict[str, set[str]] = {}
        for symbol, qty in projected_positions.items():
            if qty == 0:
                continue
            theme = self._direction_plans.get(symbol, MacroDirectionPlan(symbol, 0, 0.0, "GLOBAL", "MIXED", "EQUITY", "NONE", self.enter_threshold, self.exit_threshold, self.risk_pct, self.max_notional_pct)).theme
            active_theme_symbols.setdefault(theme, set()).add(symbol)

        reductions: list[MacroTargetCandidate] = []
        increases: list[MacroTargetCandidate] = []

        for symbol in self.symbols:
            if symbol in blocked_symbols:
                continue
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            atr = _atr(highs, lows, closes, self.atr_period)
            if atr is None:
                continue

            plan = self._direction_plans.get(
                symbol,
                MacroDirectionPlan(
                    symbol=symbol,
                    sign=0,
                    score=0.0,
                    region=region_map.get(symbol, "GLOBAL"),
                    theme=region_themes.get(region_map.get(symbol, "GLOBAL"), "MIXED"),
                    asset_class=asset_map.get(symbol, "EQUITY"),
                    reason="NO_PLAN",
                    entry_threshold=self.enter_threshold,
                    exit_threshold=self.exit_threshold,
                    risk_pct=self.risk_pct,
                    max_notional_pct=self.max_notional_pct,
                ),
            )
            current_qty = int(self.portfolio.position_for_symbol(symbol).quantity)
            target_qty = self._target_quantity_from_plan(
                symbol=symbol,
                plan=plan,
                bar=bar,
                atr=atr,
                closes=closes,
            )
            if not self._passes_resize_hysteresis(current_qty=current_qty, desired_qty=target_qty):
                continue

            reason = plan.reason
            if current_qty != 0 and target_qty == 0 and reason not in {"STALE_MACRO", "NO_MACRO"}:
                reason = "REGIME_EXIT"
            elif current_qty == 0 and target_qty != 0 and reason in {"DIRECTIONAL", "RELATIVE_DOMINANT", "RELATIVE"}:
                reason = "REGIME_ENTRY"

            if current_qty * target_qty < 0:
                reductions.append(
                    MacroTargetCandidate(
                        symbol=symbol,
                        desired_qty=0,
                        price=bar.close,
                        score=plan.score,
                        region=plan.region,
                        asset_class=plan.asset_class,
                        theme=plan.theme,
                        reason="REGIME_FLIP_EXIT",
                    )
                )
                increases.append(
                    MacroTargetCandidate(
                        symbol=symbol,
                        desired_qty=target_qty,
                        price=bar.close,
                        score=plan.score,
                        region=plan.region,
                        asset_class=plan.asset_class,
                        theme=plan.theme,
                        reason="REGIME_FLIP_ENTRY",
                    )
                )
                continue

            candidate = MacroTargetCandidate(
                symbol=symbol,
                desired_qty=target_qty,
                price=bar.close,
                score=plan.score,
                region=plan.region,
                asset_class=plan.asset_class,
                theme=plan.theme,
                reason=reason,
            )
            if abs(target_qty) < abs(current_qty):
                reductions.append(candidate)
            else:
                increases.append(candidate)

        for candidate in reductions:
            current_qty = int(projected_positions.get(candidate.symbol, 0))
            signal = self._emit_target_delta(
                market_event=market_event,
                symbol=candidate.symbol,
                current_qty=current_qty,
                desired_qty=candidate.desired_qty,
                score=candidate.score,
                reason=candidate.reason,
            )
            if signal is None:
                continue
            signals.append(signal)
            projected_positions[candidate.symbol] = candidate.desired_qty
            if candidate.desired_qty == 0:
                active_theme_symbols.get(candidate.theme, set()).discard(candidate.symbol)

        for candidate in sorted(increases, key=lambda item: abs(item.score), reverse=True):
            theme_symbols = active_theme_symbols.setdefault(candidate.theme, set())
            current_qty = int(projected_positions.get(candidate.symbol, 0))
            if current_qty == 0 and len(theme_symbols) >= self.max_theme_expressions:
                continue
            fitted_qty = self._fit_target_qty(
                symbol=candidate.symbol,
                desired_qty=candidate.desired_qty,
                projected_positions=projected_positions,
                prices=prices,
                region_map=region_map,
                asset_map=asset_map,
                equity=equity,
            )
            if fitted_qty == current_qty:
                continue
            signal = self._emit_target_delta(
                market_event=market_event,
                symbol=candidate.symbol,
                current_qty=current_qty,
                desired_qty=fitted_qty,
                score=candidate.score,
                reason=candidate.reason,
            )
            if signal is None:
                continue
            signals.append(signal)
            projected_positions[candidate.symbol] = fitted_qty
            if fitted_qty != 0:
                theme_symbols.add(candidate.symbol)

        return signals


class BarbellMacroWizardStrategy(TopDownGlobalMacroWizardStrategy):
    """
    Barbell macro architecture:
    - Engine A is an always-on credit carry / beta sleeve in HYG and LQD with an explicit shutoff budget.
    - Engine B is a concentrated, event-driven dislocation sleeve that only deploys in coherent macro breaks.
    """

    def __init__(
        self,
        symbols: list[str],
        macro_source: MacroDataSource,
        security_master: CSVSecurityMasterDataSource,
        futures_curve_source: FuturesCurveDataSource | None = None,
        macro_surprise_source: MacroSurpriseDataSource | None = None,
        carry_symbols: tuple[str, ...] = ("HYG", "LQD"),
        dislocation_symbols: tuple[str, ...] = ("SPY", "EEM", "DBC", "USO", "UUP"),
        carry_target_weights: dict[str, float] | None = None,
        carry_drawdown_lookback: int = 20,
        carry_drawdown_stop: float = 0.03,
        carry_credit_z_stop: float = -1.5,
        carry_pnl_lookback_days: int = 60,
        carry_pnl_budget_pct: float = 0.02,
        carry_reentry_start: float = 0.50,
        carry_reentry_weeks: int = 4,
        engine_b_probe_threshold: float = 0.80,
        engine_b_conviction_threshold: float = 1.20,
        engine_b_max_threshold: float = 1.80,
        engine_b_max_gross: float = 2.15,
        engine_b_top_n: int = 3,
        enable_velocity_layer: bool = False,
        enable_phase_stop_layer: bool = False,
        enable_engine_a_reallocation_layer: bool = False,
        enable_asymmetric_phase_timing_layer: bool = False,
        enable_extreme_concentration_layer: bool = False,
        enable_phase3_profit_rate_layer: bool = False,
        enable_focused_reallocation_layer: bool = False,
        enable_overlay_routing_layer: bool = False,
        overlay_off: bool = False,
        enable_conditional_hedge_layer: bool = False,
        enable_time_series_momentum_layer: bool = False,
        enable_rates_curve_layer: bool = False,
        enable_commodity_futures_sleeve_layer: bool = False,
        enable_convex_dislocation_layer: bool = False,
        enable_crisis_trend_layer: bool = False,
        enable_dollar_squeeze_layer: bool = False,
        enable_liquidation_reversal_layer: bool = False,
        enable_optimization_layer: bool = False,
        enable_portfolio_vol_targeting_layer: bool = False,
        enable_adaptive_threshold_layer: bool = False,
        enable_correlation_selection_layer: bool = False,
        enable_asymmetric_trend_filter_layer: bool = False,
        enable_dynamic_engine_a_layer: bool = False,
        enable_phase4_layer: bool = False,
        enable_cross_asset_confirmation_layer: bool = False,
        enable_cross_sleeve_amplifier_layer: bool = False,
        enable_macro_surprise_layer: bool = False,
        enable_market_evidence_layer: bool = False,
        enable_engine_b_core: bool = True,
        enable_cheap_discovery_layer: bool = False,
        enable_extreme_phase_skip_layer: bool = False,
        enable_melt_up_reversal_layer: bool = False,
        enable_carry_crash_risk_layer: bool = False,
        enable_drawdown_throttle_layer: bool = False,
        enable_nav_stop_layer: bool = False,
        sensor_symbols: tuple[str, ...] = ("EMB",),
        hedge_symbols: tuple[str, ...] = ("TLT", "IEF", "GLD", "TIP"),
        trend_sleeve_symbols: tuple[str, ...] = ("SPY", "EEM", "TLT", "IEF", "GLD", "DBC", "USO", "UUP"),
        rates_curve_symbols: tuple[str, ...] = ("ZT=F", "ZN=F", "ZB=F"),
        rates_proxy_symbols: tuple[str, ...] = ("IEF", "TLT"),
        rates_curve_max_gross: float = 0.45,
        rates_curve_max_symbol_weight: float = 0.20,
        rates_curve_duration_long_entry_score: float = 1.25,
        rates_curve_duration_short_entry_score: float = 0.95,
        rates_curve_duration_long_gross_mult: float = 0.35,
        enable_rates_rolling_ev_gate_layer: bool = False,
        rates_rolling_ev_horizon: int = 21,
        rates_rolling_ev_min_obs: int = 30,
        rates_rolling_ev_min_mean: float = 0.0,
        enable_curve_rv_layer: bool = False,
        curve_rv_max_gross: float = 0.20,
        curve_rv_max_leg_weight: float = 0.10,
        curve_rv_regime_entry_score: float = 1.50,
        curve_rv_slope_z_entry: float = 0.50,
        commodity_futures_symbols: tuple[str, ...] = ("CL=F", "GC=F", "HG=F"),
        instrument_multipliers: dict[str, float] | None = None,
        macro_sensor_series: tuple[str, ...] = ("VIXCLS", "T10Y2Y", "BAMLEMCBPIOAS"),
        engine_b_velocity_boost_signal: float = 0.75,
        engine_b_velocity_boost_multiplier: float = 1.20,
        engine_b_velocity_coherence_floor: float = 0.50,
        engine_b_velocity_exit_signal: float = 0.15,
        engine_b_velocity_exit_peak_ratio: float = 0.40,
        engine_b_velocity_score_decay: float = 0.90,
        engine_b_velocity_peak_floor: float = 0.75,
        trailing_atr_mult: float = 3.0,
        atr_period: int = 20,
        trend_ma: int = 200,
        z_window: int = 36,
        z_window_max: int = 72,
        min_macro_observations: int = 24,
        trend_penalty: float = 0.50,
        max_gross_total: float = 2.5,
        max_net_total: float = 1.2,
        max_gross_per_asset_class: float = 0.70,
        max_gross_per_region: float = 0.60,
        stale_after_days: int = 45,
        stale_half_life_days: int = 30,
        adaptive_weight_lookback: int = 60,
        adaptive_weight_min_obs: int = 36,
        use_adaptive_weights: bool = False,
        macro_inputs_are_zscores: bool = False,
    ) -> None:
        optimization_layer = bool(enable_optimization_layer)
        super().__init__(
            symbols=symbols,
            macro_source=macro_source,
            security_master=security_master,
            atr_period=atr_period,
            trend_ma=trend_ma,
            z_window=z_window,
            z_window_max=z_window_max,
            min_macro_observations=min_macro_observations,
            enter_threshold=engine_b_probe_threshold,
            exit_threshold=engine_b_probe_threshold * 0.5,
            trailing_atr_mult=trailing_atr_mult,
            risk_pct=0.01,
            relative_risk_pct=0.005,
            max_notional_pct=0.40,
            carry_risk_pct=0.0,
            carry_max_notional_pct=0.0,
            carry_exit_threshold=0.0,
            trend_penalty=trend_penalty,
            conviction_power=1.0,
            max_gross_total=max_gross_total,
            max_net_total=max_net_total,
            max_gross_per_asset_class=max_gross_per_asset_class,
            max_gross_per_region=max_gross_per_region,
            max_theme_expressions=engine_b_top_n,
            stale_after_days=stale_after_days,
            stale_half_life_days=stale_half_life_days,
            adaptive_weight_lookback=adaptive_weight_lookback,
            adaptive_weight_min_obs=adaptive_weight_min_obs,
            macro_inputs_are_zscores=macro_inputs_are_zscores,
        )
        self.carry_symbols = tuple(symbol.upper() for symbol in carry_symbols if symbol.upper() in self.symbols)
        self.futures_curve_source = futures_curve_source
        self.macro_surprise_source = macro_surprise_source
        self.dislocation_symbols = tuple(
            symbol.upper() for symbol in dislocation_symbols if symbol.upper() in self.symbols
        )
        self.sensor_symbols = tuple(symbol.upper() for symbol in sensor_symbols if symbol.upper() in self.symbols)
        self.hedge_symbols = tuple(symbol.upper() for symbol in hedge_symbols if symbol.upper() in self.symbols)
        self.trend_sleeve_symbols = tuple(
            symbol.upper() for symbol in trend_sleeve_symbols if symbol.upper() in self.symbols
        )
        self.rates_curve_symbols = tuple(symbol.upper() for symbol in rates_curve_symbols if symbol.upper() in self.symbols)
        self.rates_proxy_symbols = tuple(symbol.upper() for symbol in rates_proxy_symbols if symbol.upper() in self.symbols)
        self.commodity_futures_symbols = tuple(
            symbol.upper() for symbol in commodity_futures_symbols if symbol.upper() in self.symbols
        )
        self.instrument_multipliers = {
            symbol.upper(): max(float(multiplier), 1e-9)
            for symbol, multiplier in (instrument_multipliers or {}).items()
        }
        self.macro_sensor_series = tuple(series.upper() for series in macro_sensor_series)
        self._research_closes = {symbol: [] for symbol in self.symbols}
        self._research_dates = {symbol: [] for symbol in self.symbols}
        default_carry = (
            {"HYG": 0.18, "LQD": 0.12}
            if optimization_layer
            else {symbol: 0.15 for symbol in self.carry_symbols}
        )
        if carry_target_weights is not None:
            default_carry.update({symbol.upper(): float(weight) for symbol, weight in carry_target_weights.items()})
        self.carry_target_weights = {
            symbol: max(float(default_carry.get(symbol, 0.0)), 0.0)
            for symbol in self.carry_symbols
        }
        self.carry_drawdown_lookback = max(int(carry_drawdown_lookback), 2)
        self.carry_drawdown_stop = max(float(carry_drawdown_stop), 0.0)
        self.carry_credit_z_stop = float(carry_credit_z_stop)
        self.carry_pnl_lookback_days = max(int(carry_pnl_lookback_days), 1)
        self.carry_pnl_budget_pct = max(float(carry_pnl_budget_pct), 0.0)
        self.carry_reentry_start = min(max(float(carry_reentry_start), 0.0), 1.0)
        self.carry_reentry_weeks = max(int(carry_reentry_weeks), 1)
        self.engine_b_probe_threshold = float(engine_b_probe_threshold)
        self.engine_b_conviction_threshold = max(float(engine_b_conviction_threshold), self.engine_b_probe_threshold)
        self.engine_b_max_threshold = max(float(engine_b_max_threshold), self.engine_b_conviction_threshold)
        self.engine_b_max_gross = max(float(engine_b_max_gross), 0.0)
        self.engine_b_top_n = max(int(engine_b_top_n), 1)
        self.enable_phase_stop_layer = bool(enable_phase_stop_layer)
        self.enable_velocity_layer = bool(enable_velocity_layer or enable_phase_stop_layer)
        self.enable_engine_a_reallocation_layer = bool(enable_engine_a_reallocation_layer and enable_phase_stop_layer)
        self.enable_asymmetric_phase_timing_layer = bool(enable_asymmetric_phase_timing_layer and enable_phase_stop_layer)
        self.enable_extreme_concentration_layer = bool(enable_extreme_concentration_layer and self.enable_engine_a_reallocation_layer)
        self.enable_phase3_profit_rate_layer = bool(enable_phase3_profit_rate_layer and enable_phase_stop_layer)
        self.enable_focused_reallocation_layer = bool(
            enable_focused_reallocation_layer
            and self.enable_engine_a_reallocation_layer
            and self.enable_extreme_concentration_layer
        )
        self.enable_overlay_routing_layer = bool(enable_overlay_routing_layer)
        self.overlay_off = bool(overlay_off)
        self.enable_conditional_hedge_layer = bool(enable_conditional_hedge_layer)
        self.enable_time_series_momentum_layer = bool(enable_time_series_momentum_layer)
        self.enable_rates_curve_layer = bool(enable_rates_curve_layer)
        self.enable_rates_rolling_ev_gate_layer = bool(enable_rates_rolling_ev_gate_layer)
        self.enable_curve_rv_layer = bool(enable_curve_rv_layer)
        self.enable_commodity_futures_sleeve_layer = bool(enable_commodity_futures_sleeve_layer)
        self.enable_convex_dislocation_layer = bool(enable_convex_dislocation_layer and enable_phase_stop_layer)
        self.enable_crisis_trend_layer = bool(enable_crisis_trend_layer)
        self.enable_dollar_squeeze_layer = bool(enable_dollar_squeeze_layer)
        self.enable_liquidation_reversal_layer = bool(enable_liquidation_reversal_layer)
        self.enable_optimization_layer = optimization_layer
        self.enable_portfolio_vol_targeting_layer = bool(enable_portfolio_vol_targeting_layer or optimization_layer)
        self.enable_adaptive_threshold_layer = bool(enable_adaptive_threshold_layer or optimization_layer)
        self.enable_correlation_selection_layer = bool(enable_correlation_selection_layer or optimization_layer)
        self.enable_asymmetric_trend_filter_layer = bool(enable_asymmetric_trend_filter_layer or optimization_layer)
        self.enable_dynamic_engine_a_layer = bool(enable_dynamic_engine_a_layer or optimization_layer)
        self.enable_phase4_layer = bool(enable_phase4_layer or optimization_layer)
        self.enable_cross_asset_confirmation_layer = bool(enable_cross_asset_confirmation_layer or optimization_layer)
        self.enable_cross_sleeve_amplifier_layer = bool(enable_cross_sleeve_amplifier_layer)
        self.enable_macro_surprise_layer = bool(enable_macro_surprise_layer and macro_surprise_source is not None)
        self.enable_market_evidence_layer = bool(enable_market_evidence_layer)
        self.enable_engine_b_core = bool(enable_engine_b_core)
        self.enable_cheap_discovery_layer = bool(enable_cheap_discovery_layer and enable_phase_stop_layer)
        self.enable_extreme_phase_skip_layer = bool(enable_extreme_phase_skip_layer and self.enable_phase_stop_layer)
        self.enable_melt_up_reversal_layer = bool(enable_melt_up_reversal_layer or optimization_layer)
        self.enable_carry_crash_risk_layer = bool(enable_carry_crash_risk_layer)
        self.enable_drawdown_throttle_layer = bool(enable_drawdown_throttle_layer or optimization_layer)
        self.enable_nav_stop_layer = bool(enable_nav_stop_layer or optimization_layer)
        self.engine_b_velocity_boost_signal = max(float(engine_b_velocity_boost_signal), 0.0)
        self.engine_b_velocity_boost_multiplier = max(float(engine_b_velocity_boost_multiplier), 1.0)
        self.engine_b_velocity_coherence_floor = min(max(float(engine_b_velocity_coherence_floor), 0.0), 1.0)
        self.engine_b_velocity_exit_signal = max(float(engine_b_velocity_exit_signal), 0.0)
        self.engine_b_velocity_exit_peak_ratio = min(max(float(engine_b_velocity_exit_peak_ratio), 0.0), 1.0)
        self.engine_b_velocity_score_decay = min(max(float(engine_b_velocity_score_decay), 0.0), 1.0)
        self.engine_b_velocity_peak_floor = max(float(engine_b_velocity_peak_floor), 0.0)
        self.engine_b_phase1_days = 10
        self.engine_b_phase2_days = 30
        self.engine_b_phase1_profit_atr = 1.0
        self.engine_b_phase2_profit_atr = 3.0
        self.engine_b_phase3_min_days = 12
        self.engine_b_phase3_profit_rate_atr = 0.12
        self.engine_b_crisis_phase1_days = 5
        self.engine_b_crisis_phase1_profit_atr = 0.75
        self.engine_b_phase3_coherence_floor = 0.50
        self.engine_b_phase4_days = 25
        self.engine_b_phase4_profit_atr = 6.0
        self.engine_b_phase4_profit_rate_atr = 0.20
        self.engine_b_phase4_coherence_floor = 0.70
        self.engine_b_phase_skip_score = 2.50
        self.engine_b_phase_skip_coherence = 0.85
        self.engine_b_phase1_weight_mult = 0.50
        self.engine_b_phase2_weight_mult = 1.00
        self.engine_b_phase3_weight_mult = 1.30
        self.engine_b_phase4_weight_mult = 1.80
        self.engine_b_extreme_phase3_weight_mult = 1.50
        self.engine_b_extreme_phase4_weight_mult = 2.00
        self.engine_b_phase1_stop_atr = 1.50
        self.engine_b_phase2_stop_atr = 2.50
        self.engine_b_phase3_stop_atr = 3.50
        self.engine_b_phase4_stop_atr = 4.50
        if self.enable_cheap_discovery_layer:
            # Phase 1 buys information cheaply; confirmed Phase 2/3 trades retain full size.
            self.engine_b_phase1_weight_mult = 0.15
        self.engine_b_extreme_top_n = 2
        self.engine_b_extreme_coherence_quantile = 0.90
        self.engine_b_extreme_coherence_min_obs = 40
        if optimization_layer:
            self.engine_b_extreme_top_n = 3
        self.overlay_phase1_days = 5
        self.overlay_phase1_profit_atr = 0.50
        self.overlay_phase1_weight_mult = 0.50
        self.overlay_phase2_weight_mult = 1.00
        self.overlay_phase1_stop_atr = 1.50
        self.overlay_phase2_stop_atr = 2.50
        self.overlay_lag_close_ratio = 0.75
        self.overlay_lag_fail_ratio = 1.10
        self.overlay_symbols = tuple(
            symbol
            for symbol in self.dislocation_symbols
            if symbol in {"SPY", "EEM", "DBC", "USO", "CL=F", "GC=F", "HG=F", "ZC=F"}
        )
        self.overlay_holding_periods = {
            "SPY": 20,
            "EEM": 25,
            "DBC": 20,
            "USO": 15,
            "CL=F": 15,
            "GC=F": 20,
            "HG=F": 20,
            "ZC=F": 20,
        }
        self.overlay_leader_quantile = 0.75
        self.overlay_leader_threshold_floor = 0.60
        self.overlay_leader_threshold_cap = 1.20
        self.overlay_leader_threshold_fallback = 0.70
        self.overlay_leader_min_obs = 40
        self.overlay_exit_lag = 0.25
        self.overlay_max_core_fraction = 0.50
        self.overlay_max_asset_weight = 0.85
        self.overlay_routing_min_weight = 0.02
        self.overlay_routing_max_donor_fraction = 0.50
        self.overlay_routing_min_residual_weight = 0.05
        self.hedge_core_gross_threshold = 0.45
        self.hedge_max_gross = 0.15
        self.hedge_max_symbol_weight = 0.06
        self.hedge_min_score = 0.35
        self.trend_sleeve_max_gross = 0.12
        self.trend_sleeve_active_core_gross = 0.05
        self.trend_sleeve_max_symbol_weight = 0.04
        self.trend_sleeve_min_abs_score = 1.00
        self.trend_sleeve_vol_target = 0.18
        self.trend_sleeve_vol_lookback = 63
        self.trend_sleeve_lookbacks = (21, 63, 126, 252)
        if self.enable_market_evidence_layer:
            self.trend_sleeve_max_gross = 0.60
            self.trend_sleeve_active_core_gross = 0.30
            self.trend_sleeve_max_symbol_weight = 0.12
        self.rates_curve_max_gross = max(float(rates_curve_max_gross), 0.0)
        self.rates_curve_max_symbol_weight = max(float(rates_curve_max_symbol_weight), 0.0)
        self.rates_curve_duration_long_entry_score = max(float(rates_curve_duration_long_entry_score), 0.0)
        self.rates_curve_duration_short_entry_score = max(float(rates_curve_duration_short_entry_score), 0.0)
        self.rates_curve_duration_long_gross_mult = min(
            max(float(rates_curve_duration_long_gross_mult), 0.0),
            1.0,
        )
        self.rates_rolling_ev_horizon = max(int(rates_rolling_ev_horizon), 1)
        self.rates_rolling_ev_min_obs = max(int(rates_rolling_ev_min_obs), 1)
        self.rates_rolling_ev_min_mean = float(rates_rolling_ev_min_mean)
        self.rates_curve_entry_score = 0.75
        self.curve_rv_max_gross = max(float(curve_rv_max_gross), 0.0)
        self.curve_rv_max_leg_weight = max(float(curve_rv_max_leg_weight), 0.0)
        self.curve_rv_regime_entry_score = max(float(curve_rv_regime_entry_score), 0.0)
        self.curve_rv_slope_z_entry = max(float(curve_rv_slope_z_entry), 0.0)
        self.commodity_futures_max_gross = 0.60
        self.commodity_futures_max_symbol_weight = 0.42
        self.commodity_futures_entry_score = 1.10
        self.commodity_futures_min_momentum = 0.65
        self.commodity_futures_price_only_max_gross = 0.24
        self.commodity_futures_price_only_max_symbol_weight = 0.16
        self.commodity_futures_price_only_entry_score = 1.35
        self.commodity_futures_price_only_min_momentum = 0.90
        self.commodity_curve_annual_risk_target = 0.04
        self.commodity_curve_annual_vol_floor = 0.10
        self.engine_a_reallocation_phase_floor = 2
        self.engine_a_reallocation_deploy_fraction = 1.0
        self.engine_a_reallocation_max_symbol_weight = 1.0
        self.engine_b_convex_phase_floor = 2
        self.engine_b_convex_trigger_score = 1.80
        self.engine_b_convex_coherence_floor = 0.65
        self.engine_b_convex_vol_z_floor = 0.75
        self.engine_b_convex_symbol_weight = 0.04
        self.engine_b_convex_max_gross = 0.12
        self.engine_b_convex_max_symbol_weight = 1.15
        self.crisis_trend_base_gross = 0.0
        self.crisis_trend_stress_gross = 0.45
        self.crisis_trend_top_n = 3
        self.crisis_trend_min_score = 1.25
        self.crisis_trend_max_symbol_weight = 0.25
        self.dollar_squeeze_max_gross = 0.35
        self.dollar_squeeze_trigger_z = 1.25
        self.dollar_squeeze_hyg_drawdown = -0.025
        self.dollar_squeeze_max_symbol_weight = 0.22
        self.reversal_max_gross = 0.35
        self.reversal_top_n = 2
        self.reversal_drawdown_threshold = -0.12
        self.reversal_confirmation_lookback = 5
        self.reversal_max_symbol_weight = 0.18
        self.reversal_max_hold_bars = 40
        self.reversal_stop_pct = 0.07
        if optimization_layer:
            self.reversal_max_gross = 0.45
            self.reversal_max_symbol_weight = 0.22
            self.reversal_stop_pct = 0.06
        self.melt_up_max_gross = 0.20
        self.melt_up_top_n = 1
        self.melt_up_threshold = 0.20
        self.melt_up_max_symbol_weight = 0.12
        self.melt_up_stop_pct = 0.06
        self.melt_up_max_hold_bars = 30
        self.target_portfolio_vol = 0.18
        self.vol_lookback = 63
        self.vol_scale_min = 0.50
        self.vol_scale_max = 1.60
        self.dynamic_engine_a_max_gross = 0.50
        self.carry_crash_min_scale = 0.20
        self.carry_crash_lqd_share_max = 0.75
        self.carry_crash_vol_threshold = 0.18
        self.carry_crash_trend_lookback = 100
        self.position_nav_stop = 0.025
        self.cross_asset_boost_2 = 1.10
        self.cross_asset_boost_3 = 1.25
        self.cross_sleeve_min_alignment_count = 4
        self.cross_sleeve_coherence_floor = 0.80
        self.cross_sleeve_requires_confirmed_position = True
        self.cross_sleeve_gross_multiplier = 1.20
        self.cross_sleeve_engine_b_max_gross = 2.65
        self.cross_sleeve_phase3_weight_mult = 1.60
        self.macro_surprise_factor_confirm_mult = 1.20
        self.macro_surprise_factor_disagree_mult = 0.85
        self.macro_surprise_confirm_threshold = 1.50
        self.macro_surprise_lookback_days = 84
        self.macro_surprise_half_lives = {
            "growth": 21.0,
            "inflation": 21.0,
            "policy": 7.0,
            "liquidity": 7.0,
            "credit": 14.0,
        }
        # In the robust redesign, tradable-market evidence determines direction.
        # Macro observations remain available for attribution, but cannot flip a trade.
        self.market_evidence_lookbacks = (21, 63, 126, 252)
        self.market_evidence_weights = (0.10, 0.20, 0.30, 0.40)
        self.market_evidence_score_scale = 1.50
        self.market_evidence_rates_entry_score = 0.65
        self.market_evidence_commodity_entry_score = 0.75
        self.market_evidence_commodity_momentum_floor = 0.50
        self.market_evidence_short_shock_floor = 0.75
        self.market_evidence_medium_shock_floor = 0.50
        self.market_evidence_hold_score_floor = 0.35
        self.market_evidence_phase3_score_floor = 2.50
        self.use_adaptive_weights = bool(use_adaptive_weights)
        self._engine_a_shutoff_active = False
        self._engine_a_reentry_step: int | None = None
        self._engine_a_last_trigger_reason = "CARRY_BASE"
        self._engine_a_last_trigger_metrics: dict[str, float] = {}
        self._last_engine_b_feedback: dict[str, float | bool] = {
            "engine_a_pnl_5d_ratio": 0.0,
            "engine_a_pnl_21d_ratio": 0.0,
            "a_stress": False,
            "a_euphoria": False,
            "emb_5d_z": 0.0,
            "emb_stress": False,
            "emb_euphoria": False,
            "long_threshold_multiplier": 1.0,
            "short_threshold_multiplier": 1.0,
        }
        self._engine_a_pnl_history: deque[tuple[date, float]] = deque(maxlen=2_000)
        self._engine_a_pnl_5d_ratio_history: deque[float] = deque(maxlen=2_000)
        self._overlay_leader_history = {
            symbol: deque(maxlen=756)
            for symbol in self.overlay_symbols
        }
        self._overlay_state: dict[str, dict[str, float | int]] = {}
        self._reversal_state: dict[str, dict[str, float | int]] = {}
        self._melt_up_state: dict[str, dict[str, float | int]] = {}
        self._overlay_last_snapshot: dict[str, dict[str, float | str | bool]] = {}
        self._engine_b_state: dict[str, dict[str, float | int]] = {}
        self._core_barbell_plans: dict[str, BarbellTargetPlan] = {}
        self._last_barbell_plans: dict[str, BarbellTargetPlan] = {}
        self._barbell_daily_rows: list[dict[str, float | str | bool]] = []
        self._candidate_research_rows: list[dict[str, float | str | bool]] = []
        self._engine_b_coherence_history: deque[float] = deque(maxlen=1_000)
        self._last_engine_b_scale = 0.0
        self._last_engine_b_max_score = 0.0
        self._last_engine_b_velocity_multiplier = 1.0
        self._last_engine_b_velocity_signal = 0.0
        self._last_engine_b_extreme_active = False
        self._last_engine_b_extreme_coherence = 0.0
        self._last_engine_b_extreme_threshold = 0.0
        self._last_engine_b_phase_skip_count = 0
        self._last_engine_a_reallocation_gross = 0.0
        self._last_engine_a_reallocation_count = 0
        self._last_engine_b_convex_gross = 0.0
        self._last_engine_b_convex_count = 0
        self._last_conditional_hedge_gross = 0.0
        self._last_conditional_hedge_count = 0
        self._last_trend_sleeve_gross = 0.0
        self._last_trend_sleeve_count = 0
        self._last_trend_sleeve_rebalance: tuple[int, int] | None = None
        self._trend_sleeve_weights: dict[str, float] = {}
        self._last_rates_curve_gross = 0.0
        self._last_rates_curve_count = 0
        self._last_rates_curve_futures_gross = 0.0
        self._last_rates_curve_proxy_gross = 0.0
        self._last_rates_curve_futures_count = 0
        self._last_rates_curve_proxy_count = 0
        self._last_rates_curve_zero_contract_fallback_count = 0
        self._last_rates_curve_missing_market_count = 0
        self._last_rates_curve_trend_filter_count = 0
        self._rates_rolling_ev_history: dict[str, deque[float]] = {}
        self._rates_rolling_ev_pending: deque[dict[str, float | int | str]] = deque(maxlen=5_000)
        self._last_rates_ev_gate_block_count = 0
        self._last_rates_ev_gate_pass_count = 0
        self._last_rates_ev_gate_ready_count = 0
        self._last_rates_ev_gate_mean = 0.0
        self._last_curve_rv_gross = 0.0
        self._last_curve_rv_count = 0
        self._last_curve_rv_futures_gross = 0.0
        self._last_curve_rv_proxy_gross = 0.0
        self._last_curve_rv_futures_count = 0
        self._last_curve_rv_proxy_count = 0
        self._last_curve_rv_slope_z = 0.0
        self._last_curve_rv_slope_momentum = 0.0
        self._last_curve_rv_regime_score = 0.0
        self._last_commodity_futures_gross = 0.0
        self._last_commodity_futures_count = 0
        self._last_commodity_futures_reject_counts = {
            "missing_curve": 0,
            "stale_curve": 0,
            "weak_curve": 0,
            "low_liquidity": 0,
            "weak_macro": 0,
            "weak_momentum": 0,
            "momentum_mismatch": 0,
            "carry_mismatch": 0,
        }
        self._last_crisis_trend_gross = 0.0
        self._last_crisis_trend_count = 0
        self._last_dollar_squeeze_gross = 0.0
        self._last_dollar_squeeze_count = 0
        self._last_reversal_gross = 0.0
        self._last_reversal_count = 0
        self._last_melt_up_gross = 0.0
        self._last_melt_up_count = 0
        self._last_vol_target_scalar = 1.0
        self._last_drawdown_throttle_scalar = 1.0
        self._last_portfolio_scalar = 1.0
        self._last_portfolio_vol_estimate = 0.0
        self._last_cross_asset_boost = 1.0
        self._last_cross_sleeve_amplifier_active = False
        self._last_cross_sleeve_alignment_count = 0
        self._last_cross_sleeve_direction = 0
        self._last_cross_sleeve_gross_multiplier = 1.0
        self._macro_surprise_state_cache: dict[tuple[str, date], dict[str, object]] = {}
        self._last_nav_stop_count = 0
        self._peak_equity = 0.0
        self._bar_index = 0

    def _instrument_multiplier(self, symbol: str) -> float:
        return float(self.instrument_multipliers.get(symbol.upper(), 1.0))

    def _instrument_notional(self, symbol: str, quantity: float, price: float) -> float:
        return float(quantity) * float(price) * self._instrument_multiplier(symbol)

    def _alignment_score(self, zscores: dict[str, float]) -> float:
        values = np.array(list(zscores.values()), dtype=float)
        denom = max(float(np.mean(np.abs(values))), 1e-9)
        return float(min(abs(float(np.mean(values))) / denom, 1.0))

    def _market_evidence_state(self, symbol: str) -> dict[str, float] | None:
        """Return a volatility-normalized, multi-horizon price signal known at the current close."""
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        max_lookback = max(self.market_evidence_lookbacks)
        if len(closes) < max_lookback + 1 or np.any(closes[-(max_lookback + 1) :] <= 0.0):
            return None

        log_returns = np.diff(np.log(closes[-(max_lookback + 1) :]))
        daily_vol = float(np.std(log_returns[-63:], ddof=0))
        if daily_vol <= 1e-9:
            return None

        horizon_scores: list[float] = []
        signs: list[float] = []
        for lookback in self.market_evidence_lookbacks:
            move = float(np.log(closes[-1] / closes[-lookback - 1]))
            normalized = float(np.clip(move / (daily_vol * np.sqrt(float(lookback))), -3.0, 3.0))
            horizon_scores.append(normalized)
            signs.append(float(np.sign(normalized)))

        weights = np.array(self.market_evidence_weights, dtype=float)
        weights /= max(float(np.sum(weights)), 1e-9)
        directional = float(np.dot(weights, np.array(horizon_scores, dtype=float)))
        agreement = float(abs(np.sum(signs)) / max(len(signs), 1))
        score = float(
            np.clip(
                self.market_evidence_score_scale * directional * (0.65 + 0.35 * agreement),
                -3.0,
                3.0,
            )
        )
        return {
            "score": score,
            "agreement": agreement,
            "velocity": abs(score),
            "daily_vol": daily_vol,
            "short_score": horizon_scores[0],
            "medium_score": horizon_scores[1],
        }

    def _market_duration_evidence_score(self) -> float | None:
        """Aggregate one independent market signal from each duration bucket."""
        buckets = (
            ("ZN=F", "IEF", "^TNX"),
            ("ZB=F", "TLT", "^TYX"),
            ("ZT=F", "2YY=F", "^FVX"),
        )
        scores: list[float] = []
        for candidates in buckets:
            for symbol in candidates:
                state = self._market_evidence_state(symbol)
                if state is None:
                    continue
                score = float(state["score"])
                if symbol in RATE_YIELD_INSTRUMENTS:
                    score = -score
                scores.append(score)
                break
        if not scores:
            return None
        return float(np.median(np.array(scores, dtype=float)))

    def _sleeve_total_pnl(self, symbols: tuple[str, ...], bars: dict[str, object]) -> float:
        if self.portfolio is None:
            return 0.0
        total = 0.0
        for symbol in symbols:
            position = self.portfolio.position_for_symbol(symbol)
            total += float(position.realized_pnl)
            if position.quantity == 0 or position.avg_price <= 0:
                continue
            bar = bars.get(symbol) or self.portfolio.latest_bars.get(symbol)
            if bar is None:
                continue
            total += (
                float(position.quantity)
                * (float(bar.close) - float(position.avg_price))
                * self._instrument_multiplier(symbol)
            )
        return total

    def _sleeve_gross_exposure_ratio(self, symbols: tuple[str, ...], bars: dict[str, object], equity: float) -> float:
        if self.portfolio is None or equity <= 0:
            return 0.0
        gross = 0.0
        for symbol in symbols:
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity == 0:
                continue
            bar = bars.get(symbol) or self.portfolio.latest_bars.get(symbol)
            if bar is None:
                continue
            gross += abs(self._instrument_notional(symbol, float(position.quantity), float(bar.close)))
        return gross / equity

    def _append_engine_a_pnl(self, *, as_of: date, bars: dict[str, object]) -> float:
        pnl = self._sleeve_total_pnl(self.carry_symbols, bars)
        if self._engine_a_pnl_history and self._engine_a_pnl_history[-1][0] == as_of:
            self._engine_a_pnl_history[-1] = (as_of, pnl)
        else:
            self._engine_a_pnl_history.append((as_of, pnl))
        return pnl

    def _engine_a_pnl_change_ratio(
        self,
        *,
        as_of: date,
        current_pnl: float,
        equity: float,
        lookback_days: int,
    ) -> float:
        if equity <= 0 or not self._engine_a_pnl_history:
            return 0.0
        cutoff = as_of - timedelta(days=max(int(lookback_days), 1))
        baseline: float | None = None
        for hist_date, hist_pnl in self._engine_a_pnl_history:
            if hist_date <= cutoff:
                baseline = hist_pnl
            else:
                break
        if baseline is None:
            return 0.0
        return float((current_pnl - baseline) / equity)

    def _engine_a_trailing_pnl_ratio(self, *, as_of: date, current_pnl: float, equity: float) -> float:
        return self._engine_a_pnl_change_ratio(
            as_of=as_of,
            current_pnl=current_pnl,
            equity=equity,
            lookback_days=self.carry_pnl_lookback_days,
        )

    def _hyg_drawdown(self) -> float:
        if "HYG" not in self._closes:
            return 0.0
        closes = np.fromiter(self._closes["HYG"], dtype=float)
        if len(closes) < self.carry_drawdown_lookback:
            return 0.0
        recent = closes[-self.carry_drawdown_lookback :]
        peak = float(np.max(recent))
        if peak <= 0:
            return 0.0
        return float(recent[-1] / peak - 1.0)

    def _region_context(
        self,
        *,
        as_of: date,
        region_map: dict[str, str],
    ) -> tuple[
        dict[str, dict[str, float]],
        dict[str, object],
        dict[str, str],
        dict[str, dict[str, float]],
        dict[str, float],
        dict[str, float],
    ]:
        regions = {region_map.get(symbol, "GLOBAL") for symbol in self.symbols}
        states: dict[str, dict[str, float]] = {}
        snapshots: dict[str, object] = {}
        themes: dict[str, str] = {}
        velocities: dict[str, dict[str, float]] = {}
        velocity_signals: dict[str, float] = {}
        velocity_alignments: dict[str, float] = {}
        for region in regions:
            state = self._region_macro_state(region, as_of)
            if state is None:
                continue
            zscores = dict(state["zscores"])  # type: ignore[arg-type]
            snapshot = state["snapshot"]
            states[region] = zscores
            snapshots[region] = snapshot
            themes[region] = self._classify_theme(zscores)
            velocities[region] = dict(state["velocity"])  # type: ignore[arg-type]
            velocity_signals[region] = float(state["velocity_signal"])
            velocity_alignments[region] = float(state["velocity_alignment"])
        return states, snapshots, themes, velocities, velocity_signals, velocity_alignments

    def _update_engine_a_state(
        self,
        *,
        as_of: date,
        region_states: dict[str, dict[str, float]],
        bars: dict[str, object],
        equity: float,
    ) -> tuple[bool, bool]:
        current_pnl = self._append_engine_a_pnl(as_of=as_of, bars=bars)
        trailing_ratio = self._engine_a_trailing_pnl_ratio(as_of=as_of, current_pnl=current_pnl, equity=equity)
        hyg_drawdown = self._hyg_drawdown()
        us_credit_z = float(region_states.get("US", {}).get("credit", 0.0))

        triggered = False
        reason = "CARRY_BASE"
        if hyg_drawdown <= -self.carry_drawdown_stop:
            triggered = True
            reason = "CARRY_SHUTOFF_HYG_DRAWDOWN"
        elif not self.enable_market_evidence_layer and us_credit_z <= self.carry_credit_z_stop:
            triggered = True
            reason = "CARRY_SHUTOFF_CREDIT_Z"
        elif trailing_ratio <= -self.carry_pnl_budget_pct:
            triggered = True
            reason = "CARRY_SHUTOFF_PNL_BUDGET"

        state_changed = False
        if triggered and not self._engine_a_shutoff_active:
            self._engine_a_shutoff_active = True
            self._engine_a_reentry_step = None
            state_changed = True
        elif not triggered and self._engine_a_shutoff_active:
            self._engine_a_shutoff_active = False
            self._engine_a_reentry_step = 0
            state_changed = True

        self._engine_a_last_trigger_reason = reason
        self._engine_a_last_trigger_metrics = {
            "hyg_drawdown": hyg_drawdown,
            "credit_surprise_z": us_credit_z,
            "trailing_pnl_ratio": trailing_ratio,
        }
        return triggered, state_changed

    def _engine_a_scale(self) -> float:
        if self._engine_a_shutoff_active:
            return 0.0
        if self._engine_a_reentry_step is None:
            return 1.0
        if self.enable_dynamic_engine_a_layer:
            metrics = self._engine_a_last_trigger_metrics
            hyg_drawdown = float(metrics.get("hyg_drawdown", 0.0))
            credit_z = float(metrics.get("credit_surprise_z", 0.0))
            pnl_60d = float(metrics.get("trailing_pnl_ratio", 0.0))
            metric_scores = [
                np.clip((hyg_drawdown - (-0.05)) / ((-0.005) - (-0.05)), 0.0, 1.0),
                np.clip((credit_z - (-2.0)) / ((-0.5) - (-2.0)), 0.0, 1.0),
                np.clip((pnl_60d - (-0.03)) / (0.0 - (-0.03)), 0.0, 1.0),
            ]
            return float(min(metric_scores))
        progress = min(max(self._engine_a_reentry_step, 0), self.carry_reentry_weeks) / self.carry_reentry_weeks
        return float(min(self.carry_reentry_start + (1.0 - self.carry_reentry_start) * progress, 1.0))

    def _engine_a_target_weights(
        self,
        *,
        region_states: dict[str, dict[str, float]],
        region_themes: dict[str, str],
    ) -> dict[str, float]:
        scale = self._engine_a_scale()
        if self.enable_carry_crash_risk_layer:
            base = {symbol: self.carry_target_weights.get(symbol, 0.0) for symbol in self.carry_symbols}
            if scale <= 0.0:
                return {symbol: 0.0 for symbol in self.carry_symbols}

            us_state = region_states.get("US", {})
            credit_z = float(us_state.get("credit", 0.0))
            liquidity_z = float(us_state.get("liquidity", 0.0))
            hyg_drawdown = max(abs(self._hyg_drawdown()), 0.0)
            credit_stress = np.clip((-credit_z - 0.25) / 1.25, 0.0, 1.0)
            liquidity_stress = np.clip((-liquidity_z - 0.25) / 1.25, 0.0, 1.0)
            emb_stress = 0.0
            if self.enable_market_evidence_layer:
                # Keep the carry sleeve independent of the disputed macro mapping.
                credit_stress = 0.0
                liquidity_stress = 0.0
                emb_move = self._standardized_price_move("EMB", move_lookback=5, vol_lookback=63)
                if emb_move is not None:
                    emb_stress = float(np.clip((-float(emb_move) - 0.50) / 2.0, 0.0, 1.0))
            drawdown_stress = np.clip(hyg_drawdown / max(self.carry_drawdown_stop, 1e-9), 0.0, 1.0)

            vol_stress = 0.0
            trend_stress = 0.0
            if "HYG" in self._closes:
                closes = np.fromiter(self._closes["HYG"], dtype=float)
                if len(closes) >= 22:
                    returns = np.diff(closes[-22:]) / closes[-22:-1]
                    vol = float(np.std(returns, ddof=0) * np.sqrt(252.0))
                    vol_stress = float(np.clip((vol - self.carry_crash_vol_threshold) / 0.20, 0.0, 1.0))
                if len(closes) >= self.carry_crash_trend_lookback:
                    trend = _sma(closes, self.carry_crash_trend_lookback)
                    if trend is not None and closes[-1] < trend:
                        trend_stress = float(np.clip((trend / max(closes[-1], 1e-9) - 1.0) / 0.08, 0.0, 1.0))

            crash_risk = float(
                max(credit_stress, liquidity_stress, emb_stress, drawdown_stress, vol_stress, trend_stress)
            )
            risk_scale = scale * max(1.0 - crash_risk, self.carry_crash_min_scale)
            if crash_risk >= 0.95:
                risk_scale = 0.0

            total_base = sum(abs(weight) for weight in base.values())
            if total_base <= 0.0:
                return {symbol: 0.0 for symbol in self.carry_symbols}
            lqd_share = min(0.40 + 0.35 * crash_risk, self.carry_crash_lqd_share_max)
            out = {symbol: 0.0 for symbol in self.carry_symbols}
            if "HYG" in out:
                out["HYG"] = total_base * risk_scale * (1.0 - lqd_share)
            if "LQD" in out:
                out["LQD"] = total_base * risk_scale * lqd_share
            for symbol in self.carry_symbols:
                if symbol not in {"HYG", "LQD"}:
                    out[symbol] = base.get(symbol, 0.0) * risk_scale
            return out

        if not self.enable_dynamic_engine_a_layer:
            return {symbol: self.carry_target_weights.get(symbol, 0.0) * scale for symbol in self.carry_symbols}

        us_state = region_states.get("US", {})
        credit_z = float(us_state.get("credit", 0.0))
        base_gross = sum(abs(self.carry_target_weights.get(symbol, 0.0)) for symbol in self.carry_symbols)
        if credit_z >= 1.0:
            gross = min(self.dynamic_engine_a_max_gross, base_gross * (1.0 + 0.5 * (credit_z - 1.0)))
            hyg_share = 0.70
        elif credit_z >= 0.0:
            gross = base_gross
            hyg_share = 0.60
        elif credit_z >= -0.5:
            gross = base_gross * (1.0 + credit_z * 0.6)
            hyg_share = 0.60
        elif credit_z >= -1.0:
            gross = base_gross * 0.30
            hyg_share = 0.60
        else:
            gross = 0.0
            hyg_share = 0.60
        gross *= scale

        weights: dict[str, float] = {}
        if "HYG" in self.carry_symbols:
            weights["HYG"] = gross * hyg_share
        if "LQD" in self.carry_symbols:
            weights["LQD"] = gross * (1.0 - hyg_share)
        for symbol in self.carry_symbols:
            weights.setdefault(symbol, 0.0)
        return weights

    def _engine_b_feedback_state(self, *, as_of: date, equity: float) -> dict[str, float | bool]:
        current_pnl = self._engine_a_pnl_history[-1][1] if self._engine_a_pnl_history else 0.0
        pnl_5d_ratio = self._engine_a_pnl_change_ratio(
            as_of=as_of,
            current_pnl=current_pnl,
            equity=equity,
            lookback_days=5,
        )
        pnl_21d_ratio = self._engine_a_pnl_change_ratio(
            as_of=as_of,
            current_pnl=current_pnl,
            equity=equity,
            lookback_days=21,
        )
        a_stress = bool(pnl_5d_ratio < 0.0 and pnl_5d_ratio < pnl_21d_ratio * 0.4)
        a_euphoria = bool(pnl_5d_ratio > 0.0 and pnl_5d_ratio > pnl_21d_ratio * 0.6)
        emb_5d_z = self._standardized_price_move("EMB", move_lookback=5, vol_lookback=63) if "EMB" in self.symbols else None
        emb_stress = bool(emb_5d_z is not None and emb_5d_z <= -1.5)
        emb_euphoria = bool(emb_5d_z is not None and emb_5d_z >= 1.5)
        feedback = {
            "engine_a_pnl_5d_ratio": float(pnl_5d_ratio),
            "engine_a_pnl_21d_ratio": float(pnl_21d_ratio),
            "a_stress": a_stress,
            "a_euphoria": a_euphoria,
            "emb_5d_z": float(emb_5d_z or 0.0),
            "emb_stress": emb_stress,
            "emb_euphoria": emb_euphoria,
            "long_threshold_multiplier": 0.80 if a_euphoria else 1.0,
            "short_threshold_multiplier": 0.70 if a_stress else 1.0,
        }
        self._last_engine_b_feedback = feedback
        return feedback

    def _sensor_threshold_multiplier(
        self,
        *,
        symbol: str,
        score: float,
        base_multiplier: float,
        feedback: dict[str, float | bool],
    ) -> float:
        multiplier = float(base_multiplier)
        symbol = symbol.upper()
        if score < 0.0 and bool(feedback.get("emb_stress", False)) and symbol in {
            "EEM",
            "DBC",
            "USO",
            "CL=F",
            "HG=F",
            "ZC=F",
        }:
            multiplier *= 0.80
        elif score > 0.0 and bool(feedback.get("emb_euphoria", False)) and symbol in {"EEM", "DBC", "HG=F"}:
            multiplier *= 0.90
        return multiplier

    def _engine_b_score(
        self,
        *,
        symbol: str,
        as_of: date,
        sector_map: dict[str, str],
        region_map: dict[str, str],
        asset_map: dict[str, str],
        region_states: dict[str, dict[str, float]],
        region_snapshots: dict[str, object],
        region_themes: dict[str, str],
        region_velocity_signals: dict[str, float],
        relative_overlays: dict[str, float],
    ) -> tuple[float, str, str, str, float, float]:
        region = region_map.get(symbol, "GLOBAL")
        asset_class = asset_map.get(symbol, "EQUITY")
        theme = region_themes.get(region, "MIXED")
        if self.enable_market_evidence_layer:
            evidence = self._market_evidence_state(symbol)
            if evidence is None:
                return (0.0, region, theme, "INSUFFICIENT_MARKET_EVIDENCE", 0.0, 0.0)
            score = float(evidence["score"])
            short_score = float(evidence["short_score"])
            medium_score = float(evidence["medium_score"])
            shock_confirmed = bool(
                abs(short_score) >= self.market_evidence_short_shock_floor
                and abs(medium_score) >= self.market_evidence_medium_shock_floor
                and np.sign(short_score) == np.sign(score)
                and np.sign(medium_score) == np.sign(score)
            )
            if not shock_confirmed:
                prior = self._engine_b_state.get(symbol)
                prior_sign = int(prior.get("sign", 0)) if prior is not None else 0
                evidence_sign = int(np.sign(score))
                if (
                    prior_sign != 0
                    and evidence_sign == prior_sign
                    and abs(score) >= self.market_evidence_hold_score_floor
                    and float(evidence["agreement"]) >= 0.50
                ):
                    held_score = prior_sign * max(abs(score), self.engine_b_probe_threshold)
                    return (
                        float(held_score),
                        region,
                        theme,
                        "MARKET_EVIDENCE_HOLD",
                        float(evidence["agreement"]),
                        float(evidence["velocity"]),
                    )
                return (
                    0.0,
                    region,
                    theme,
                    "MARKET_EVIDENCE_NO_SHOCK",
                    float(evidence["agreement"]),
                    0.0,
                )
            return (
                score,
                region,
                theme,
                "MARKET_EVIDENCE_TREND" if score != 0.0 else "MARKET_EVIDENCE_NEUTRAL",
                float(evidence["agreement"]),
                float(evidence["velocity"]),
            )
        if region not in region_states:
            return (0.0, region, theme, "NO_MACRO", 0.0, 0.0)

        zscores = region_states[region]
        snapshot = region_snapshots[region]
        factor_vector = np.array(
            [
                zscores["growth"],
                zscores["inflation"],
                zscores["policy"],
                zscores["liquidity"],
                zscores["credit"],
            ],
            dtype=float,
        )
        template = self._exposure_template(asset_class=asset_class, sector=sector_map.get(symbol, "UNSPECIFIED"))
        weights = (
            self._adaptive_weights(symbol=symbol, region=region, template=template, as_of=as_of)
            if self.use_adaptive_weights
            else template
        )
        factor_weights = np.array(weights, dtype=float)
        surprise_state = self._macro_surprise_state(region, as_of)
        surprise_impulses = surprise_state["impulses"]  # type: ignore[assignment]
        if self.enable_macro_surprise_layer and float(surprise_state["magnitude"]) > 0.0:
            multipliers = np.ones(len(factor_vector), dtype=float)
            for idx, name in enumerate(("growth", "inflation", "policy", "liquidity", "credit")):
                impulse = float(surprise_impulses[name])  # type: ignore[index]
                if abs(impulse) <= 1e-9 or abs(float(factor_vector[idx])) <= 1e-9:
                    continue
                if np.sign(impulse) == np.sign(float(factor_vector[idx])):
                    multipliers[idx] = self.macro_surprise_factor_confirm_mult
                else:
                    multipliers[idx] = self.macro_surprise_factor_disagree_mult
            factor_weights = factor_weights * multipliers
        raw_score = float(np.clip(np.dot(factor_weights, factor_vector), -3.0, 3.0))

        age_days = (as_of - snapshot.release_date).days
        if age_days > self.stale_after_days:
            return (0.0, region, theme, "STALE_MACRO", 0.0, 0.0)
        raw_score *= float(np.clip(getattr(snapshot, "quality_score", 1.0), 0.0, 1.0))
        if age_days > self.stale_half_life_days:
            raw_score *= 0.5

        coherence = self._alignment_score(zscores)
        if self.enable_macro_surprise_layer and float(surprise_state["magnitude"]) > 0.0:
            coherence = 0.60 * coherence + 0.40 * float(surprise_state["coherence"])
        velocity_signal = float(region_velocity_signals.get(region, 0.0))
        relative_overlay = float(relative_overlays.get(symbol, 0.0))
        theme_sign = self._theme_expression_sign(symbol=symbol, asset_class=asset_class, theme=theme)

        score = 0.0
        reason = "DISLOCATION_DORMANT"
        if theme == "MIXED":
            if symbol in {"SPY", "EEM", "UUP"} and abs(relative_overlay) >= self.engine_b_probe_threshold * 1.25:
                score = relative_overlay
                reason = "DISLOCATION_RELATIVE"
        else:
            if theme_sign != 0:
                aligned = theme_sign * raw_score
                if aligned > 0:
                    score = float(theme_sign * aligned)
                    reason = "DISLOCATION_THEME"
            if symbol in {"SPY", "EEM", "UUP"} and abs(relative_overlay) > abs(score):
                score = relative_overlay
                reason = "DISLOCATION_RELATIVE"

        adjusted_score = float(np.clip(score * (0.5 + coherence), -3.0, 3.0))
        return (adjusted_score, region, theme, reason, coherence, velocity_signal)

    def _macro_surprise_state(self, region: str, as_of: date) -> dict[str, object]:
        factors = ("growth", "inflation", "policy", "liquidity", "credit")
        empty = {
            "impulses": {factor: 0.0 for factor in factors},
            "coherence": 0.0,
            "magnitude": 0.0,
        }
        if not self.enable_macro_surprise_layer or self.macro_surprise_source is None:
            return empty
        key = (region.upper(), as_of)
        cached = self._macro_surprise_state_cache.get(key)
        if cached is not None:
            return cached
        impulses = {factor: 0.0 for factor in factors}
        events = self.macro_surprise_source.events(region, as_of, lookback_days=self.macro_surprise_lookback_days)
        for event in events:
            factor = event.factor.lower().strip()
            if factor not in impulses:
                continue
            age_days = max((as_of - event.tradable_date).days, 0)
            half_life = max(float(self.macro_surprise_half_lives.get(factor, 14.0)), 1.0)
            decay = float(np.exp(-age_days / half_life))
            impulses[factor] += float(event.surprise_z) * decay
        values = np.array([impulses[factor] for factor in factors], dtype=float)
        magnitude = float(np.sqrt(np.sum(values**2)))
        coherence = self._alignment_score(impulses) if magnitude > 0.0 else 0.0
        state = {"impulses": impulses, "coherence": coherence, "magnitude": magnitude}
        self._macro_surprise_state_cache[key] = state
        return state

    def _macro_surprise_weighted_score(
        self,
        *,
        region: str,
        as_of: date,
        weights: tuple[float, float, float, float, float],
    ) -> float:
        state = self._macro_surprise_state(region, as_of)
        impulses = state["impulses"]  # type: ignore[assignment]
        vector = np.array(
            [
                float(impulses["growth"]),  # type: ignore[index]
                float(impulses["inflation"]),  # type: ignore[index]
                float(impulses["policy"]),  # type: ignore[index]
                float(impulses["liquidity"]),  # type: ignore[index]
                float(impulses["credit"]),  # type: ignore[index]
            ],
            dtype=float,
        )
        return float(np.dot(np.array(weights, dtype=float), vector))

    def _engine_b_target_gross(
        self,
        score_mag: float,
        *,
        probe_threshold: float,
        conviction_threshold: float,
        max_threshold: float,
    ) -> float:
        if score_mag < probe_threshold:
            return 0.0
        if score_mag < conviction_threshold:
            return 0.25 * self.engine_b_max_gross
        if score_mag < max_threshold:
            return 0.60 * self.engine_b_max_gross
        return self.engine_b_max_gross

    def _adaptive_engine_b_thresholds(
        self,
        *,
        coherence: float,
        velocity_signal: float,
        probe_threshold: float,
        conviction_threshold: float,
        max_threshold: float,
    ) -> tuple[float, float, float]:
        if not self.enable_adaptive_threshold_layer:
            return probe_threshold, conviction_threshold, max_threshold
        coh_mult = 1.0 - 0.40 * max(0.0, coherence - 0.50)
        vel_mult = 1.0 - 0.20 * float(np.tanh(max(velocity_signal, 0.0)))
        multiplier = max(0.65, coh_mult * vel_mult)
        return (
            probe_threshold * multiplier,
            conviction_threshold * multiplier,
            max_threshold * multiplier,
        )

    def _symbol_return_series(self, symbol: str, lookback: int) -> pd.Series:
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        if len(closes) < lookback + 1:
            return pd.Series(dtype=float)
        returns = np.diff(closes[-(lookback + 1) :]) / closes[-(lookback + 1) : -1]
        return pd.Series(returns, dtype=float)

    def _recent_correlation(self, left: str, right: str, lookback: int = 60) -> float | None:
        left_returns = self._symbol_return_series(left, lookback)
        right_returns = self._symbol_return_series(right, lookback)
        if len(left_returns) < max(20, lookback // 2) or len(right_returns) < max(20, lookback // 2):
            return None
        corr = float(left_returns.corr(right_returns))
        return corr if np.isfinite(corr) else None

    def _correlation_aware_select(
        self,
        rows: list[tuple[str, float, str, str, str, float, float, float, float, float]],
        *,
        top_n: int,
        redundancy_penalty: float = 0.30,
    ) -> list[tuple[str, float, str, str, str, float, float, float, float, float]]:
        if not self.enable_correlation_selection_layer:
            return rows[:top_n]
        selected: list[tuple[str, float, str, str, str, float, float, float, float, float]] = []
        candidates = list(rows)
        while candidates and len(selected) < top_n:
            best: tuple[str, float, str, str, str, float, float, float, float, float] | None = None
            best_adjusted = -1.0
            for candidate in candidates:
                symbol, score, *_ = candidate
                penalty = 0.0
                for selected_row in selected:
                    corr = self._recent_correlation(symbol, selected_row[0], lookback=60)
                    if corr is not None:
                        penalty += redundancy_penalty * abs(corr)
                adjusted = abs(score) * (1.0 - min(penalty, 0.60))
                if adjusted > best_adjusted:
                    best_adjusted = adjusted
                    best = candidate
            if best is None:
                break
            selected.append(best)
            candidates.remove(best)
        return selected

    def _engine_b_velocity_multiplier(self, *, coherence: float, velocity_signal: float) -> float:
        if not self.enable_velocity_layer:
            return 1.0
        if coherence < self.engine_b_velocity_coherence_floor:
            return 1.0
        if velocity_signal < self.engine_b_velocity_boost_signal:
            return 1.0
        excess = min(
            max(
                (velocity_signal - self.engine_b_velocity_boost_signal)
                / max(self.engine_b_velocity_boost_signal, 1e-9),
                0.0,
            ),
            1.0,
        )
        return float(1.0 + excess * (self.engine_b_velocity_boost_multiplier - 1.0))

    def _velocity_decelerated(
        self,
        *,
        current_velocity: float,
        peak_velocity: float,
        current_score: float | None = None,
        peak_score: float | None = None,
    ) -> bool:
        if not self.enable_velocity_layer:
            return False
        if peak_velocity < self.engine_b_velocity_peak_floor:
            return False
        velocity_floor = max(self.engine_b_velocity_exit_signal, peak_velocity * self.engine_b_velocity_exit_peak_ratio)
        if current_velocity > velocity_floor:
            return False
        if current_score is not None and peak_score is not None and current_score > peak_score * self.engine_b_velocity_score_decay:
            return False
        return True

    def _core_phase_definition(
        self,
        *,
        symbol: str,
        plan: BarbellTargetPlan,
        bar: Bar,
        atr: float,
    ) -> tuple[int, float, float]:
        if not self.enable_phase_stop_layer:
            return (0, 1.0, self.trailing_atr_mult)
        sign = 1 if plan.core_target_weight > 0 else -1 if plan.core_target_weight < 0 else 0
        if sign == 0:
            return (0, 1.0, self.trailing_atr_mult)
        extreme_phase_skip_ready = (
            self.enable_extreme_phase_skip_layer
            and plan.extreme_regime
            and abs(float(plan.score)) >= self.engine_b_phase_skip_score
            and float(plan.coherence) >= self.engine_b_phase_skip_coherence
        )
        state = self._engine_b_state.get(symbol)
        if state is None or int(state["sign"]) != sign:
            if extreme_phase_skip_ready:
                self._last_engine_b_phase_skip_count += 1
                return (2, self.engine_b_phase2_weight_mult, self.engine_b_phase2_stop_atr)
            return (1, self.engine_b_phase1_weight_mult, self.engine_b_phase1_stop_atr)
        entry_price = float(state.get("entry_price", bar.close))
        bars_held = self._bar_index - int(state.get("entry_bar", self._bar_index))
        profit_atr = sign * (bar.close - entry_price) / max(atr, 1e-9)
        profit_rate_atr = profit_atr / max(bars_held, 1)
        if extreme_phase_skip_ready and profit_atr >= 0.0:
            self._last_engine_b_phase_skip_count += 1
            return (2, self.engine_b_phase2_weight_mult, self.engine_b_phase2_stop_atr)
        phase1_days = self.engine_b_phase1_days
        phase1_profit_atr = self.engine_b_phase1_profit_atr
        if self.enable_asymmetric_phase_timing_layer and self._is_fast_crisis_expression(symbol=symbol, sign=sign):
            phase1_days = self.engine_b_crisis_phase1_days
            phase1_profit_atr = self.engine_b_crisis_phase1_profit_atr
        if self.enable_macro_surprise_layer and plan.macro_surprise_confirmed:
            phase1_days = min(phase1_days, 5)
            phase1_profit_atr = min(phase1_profit_atr, 0.50)
        phase3_ready = (
            bars_held >= self.engine_b_phase2_days
            and profit_atr >= self.engine_b_phase2_profit_atr
            and plan.coherence >= self.engine_b_phase3_coherence_floor
            and (not self.enable_velocity_layer or plan.velocity_signal >= self.engine_b_velocity_exit_signal)
        )
        if self.enable_phase3_profit_rate_layer:
            phase3_ready = (
                bars_held >= self.engine_b_phase3_min_days
                and profit_atr >= self.engine_b_phase2_profit_atr
                and profit_rate_atr >= self.engine_b_phase3_profit_rate_atr
                and plan.coherence >= self.engine_b_phase3_coherence_floor
                and (not self.enable_velocity_layer or plan.velocity_signal >= self.engine_b_velocity_exit_signal)
            )
        if self.enable_market_evidence_layer:
            # Continuous trend signals do not deserve the dislocation engine's
            # Phase-3 leverage unless the move itself is statistically extreme.
            phase3_ready = bool(
                phase3_ready
                and abs(plan.score) >= self.market_evidence_phase3_score_floor
                and plan.coherence >= 0.75
            )
        feedback_aligned = (
            bool(self._last_engine_b_feedback.get("a_stress", False)) and sign < 0
        ) or (
            bool(self._last_engine_b_feedback.get("a_euphoria", False)) and sign > 0
        ) or plan.extreme_regime
        if self.enable_phase4_layer:
            phase4_ready = (
                bars_held >= self.engine_b_phase4_days
                and profit_atr >= self.engine_b_phase4_profit_atr
                and profit_rate_atr >= self.engine_b_phase4_profit_rate_atr
                and plan.coherence >= self.engine_b_phase4_coherence_floor
                and feedback_aligned
                and (not self.enable_velocity_layer or plan.velocity_signal >= self.engine_b_velocity_exit_signal)
            )
            if phase4_ready:
                phase4_mult = (
                    self.engine_b_extreme_phase4_weight_mult
                    if self.enable_extreme_concentration_layer and plan.extreme_regime
                    else self.engine_b_phase4_weight_mult
                )
                return (4, phase4_mult, self.engine_b_phase4_stop_atr)
        if phase3_ready:
            phase3_mult = (
                self.engine_b_extreme_phase3_weight_mult
                if self.enable_extreme_concentration_layer and plan.extreme_regime
                else self.engine_b_phase3_weight_mult
            )
            if self.enable_cross_sleeve_amplifier_layer and plan.cross_sleeve_amplifier:
                phase3_mult = max(phase3_mult, self.cross_sleeve_phase3_weight_mult)
            return (3, phase3_mult, self.engine_b_phase3_stop_atr)
        if (
            bars_held >= phase1_days
            and profit_atr >= phase1_profit_atr
            and (not self.enable_velocity_layer or plan.velocity_signal >= self.engine_b_velocity_exit_signal)
        ):
            return (2, self.engine_b_phase2_weight_mult, self.engine_b_phase2_stop_atr)
        return (1, self.engine_b_phase1_weight_mult, self.engine_b_phase1_stop_atr)

    def _is_fast_crisis_expression(self, *, symbol: str, sign: int) -> bool:
        if sign == 0:
            return False
        if symbol in {"UUP", "DX=F"} and sign > 0:
            return True
        if symbol in {"SPY", "EEM", "DBC", "USO", "CL=F", "HG=F"} and sign < 0:
            return True
        return False

    def _overlay_phase_definition(
        self,
        *,
        symbol: str,
        plan: BarbellTargetPlan,
        bar: Bar,
        atr: float,
    ) -> tuple[int, float, float, bool]:
        if not self.enable_phase_stop_layer:
            return (0, 1.0, self.trailing_atr_mult, False)
        sign = 1 if plan.overlay_target_weight > 0 else -1 if plan.overlay_target_weight < 0 else 0
        if sign == 0:
            return (0, 1.0, self.trailing_atr_mult, False)
        state = self._overlay_state.get(symbol)
        if state is None or int(state["sign"]) != sign:
            return (1, self.overlay_phase1_weight_mult, self.overlay_phase1_stop_atr, False)
        entry_price = float(state.get("entry_price", bar.close))
        bars_held = self._bar_index - int(state.get("entry_bar", self._bar_index))
        peak_lag = max(float(state.get("peak_lag", plan.lag_score)), 1e-9)
        profit_atr = sign * (bar.close - entry_price) / max(atr, 1e-9)
        lag_failed = bars_held >= 3 and plan.lag_score > peak_lag * self.overlay_lag_fail_ratio
        if lag_failed:
            return (1, self.overlay_phase1_weight_mult, self.overlay_phase1_stop_atr, True)
        if (
            bars_held >= self.overlay_phase1_days
            and profit_atr >= self.overlay_phase1_profit_atr
            and plan.lag_score <= peak_lag * self.overlay_lag_close_ratio
            and (not self.enable_velocity_layer or plan.velocity_signal >= self.engine_b_velocity_exit_signal)
        ):
            return (2, self.overlay_phase2_weight_mult, self.overlay_phase2_stop_atr, False)
        return (1, self.overlay_phase1_weight_mult, self.overlay_phase1_stop_atr, False)

    def _stop_level_for_phase(
        self,
        *,
        sign: int,
        phase: int,
        stop_mult: float,
        atr: float,
        bar: Bar,
        entry_price: float | None = None,
        peak_price: float | None = None,
    ) -> float | None:
        if sign > 0:
            if phase <= 1:
                if entry_price is None:
                    return None
                return entry_price - stop_mult * atr
            anchor = peak_price if peak_price is not None else self._high_water.get(bar.symbol)
            if anchor is None:
                return None
            return float(anchor - stop_mult * atr)
        if sign < 0:
            if phase <= 1:
                if entry_price is None:
                    return None
                return entry_price + stop_mult * atr
            anchor = peak_price if peak_price is not None else self._low_water.get(bar.symbol)
            if anchor is None:
                return None
            return float(anchor + stop_mult * atr)
        return None

    def _apply_phase_stop_architecture(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        market_event: MarketEvent,
    ) -> tuple[dict[str, BarbellTargetPlan], set[str]]:
        if not self.enable_phase_stop_layer or self.portfolio is None:
            return self._clone_barbell_plans(plans), set()

        out = self._clone_barbell_plans(plans)
        blocked_symbols: set[str] = set()
        for symbol in self.dislocation_symbols if self.enable_engine_b_core else ():
            plan = out.get(symbol)
            bar = market_event.bars.get(symbol)
            if plan is None or bar is None or plan.engine != "ENGINE_B":
                continue
            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            atr = _atr(highs, lows, closes, self.atr_period)
            if atr is None:
                continue

            qty = int(self.portfolio.position_for_symbol(symbol).quantity)
            core_phase, core_mult, core_stop_mult = self._core_phase_definition(
                symbol=symbol,
                plan=plan,
                bar=bar,
                atr=atr,
            )
            core_sign = 1 if plan.core_target_weight > 0 else -1 if plan.core_target_weight < 0 else 0
            core_stop_hit = False
            if qty != 0 and core_sign != 0 and np.sign(qty) == core_sign:
                state = self._engine_b_state.get(symbol, {})
                stop_level = self._stop_level_for_phase(
                    sign=core_sign,
                    phase=core_phase,
                    stop_mult=core_stop_mult,
                    atr=atr,
                    bar=bar,
                    entry_price=float(state.get("entry_price", bar.close)) if state else bar.close,
                )
                if stop_level is not None:
                    core_stop_hit = bool(bar.close < stop_level) if core_sign > 0 else bool(bar.close > stop_level)

            scaled_core = plan.core_target_weight * core_mult
            scaled_overlay = plan.overlay_target_weight
            overlay_phase = 0
            overlay_mult = 1.0
            if core_stop_hit:
                blocked_symbols.add(symbol)
                scaled_core = 0.0
                scaled_overlay = 0.0
                plan.convex_target_weight = 0.0
                plan.reason = "ENGINE_B_PHASE_STOP"
                plan.overlay_reason = ""
                plan.convex_reason = ""
            else:
                overlay_sign = 1 if plan.overlay_target_weight > 0 else -1 if plan.overlay_target_weight < 0 else 0
                overlay_phase, overlay_mult, overlay_stop_mult, overlay_lag_failed = self._overlay_phase_definition(
                    symbol=symbol,
                    plan=plan,
                    bar=bar,
                    atr=atr,
                )
                overlay_stop_hit = False
                if overlay_sign != 0:
                    state = self._overlay_state.get(symbol, {})
                    peak_price = float(state.get("peak_price", bar.high if overlay_sign > 0 else bar.low)) if state else None
                    stop_level = self._stop_level_for_phase(
                        sign=overlay_sign,
                        phase=overlay_phase,
                        stop_mult=overlay_stop_mult,
                        atr=atr,
                        bar=bar,
                        entry_price=float(state.get("entry_price", bar.close)) if state else bar.close,
                        peak_price=peak_price,
                    )
                    if stop_level is not None:
                        overlay_stop_hit = bool(bar.close < stop_level) if overlay_sign > 0 else bool(bar.close > stop_level)
                if overlay_lag_failed:
                    scaled_overlay = 0.0
                    plan.overlay_reason = "ENGINE_B_OVERLAY_PHASE_EXIT"
                elif overlay_stop_hit:
                    scaled_overlay = 0.0
                    plan.overlay_reason = "ENGINE_B_OVERLAY_PHASE_STOP"
                else:
                    scaled_overlay = plan.overlay_target_weight * overlay_mult

            plan.core_phase = core_phase
            plan.overlay_phase = overlay_phase
            plan.core_phase_multiplier = core_mult
            plan.overlay_phase_multiplier = overlay_mult
            plan.core_target_weight = scaled_core
            plan.overlay_target_weight = scaled_overlay
            plan.target_weight = scaled_core + scaled_overlay + plan.convex_target_weight
            if symbol in self._overlay_last_snapshot:
                snapshot = dict(self._overlay_last_snapshot[symbol])
                snapshot["overlay_weight"] = scaled_overlay
                snapshot["active"] = bool(scaled_overlay != 0.0)
                if plan.overlay_reason:
                    snapshot["reason"] = plan.overlay_reason
                self._overlay_last_snapshot[symbol] = snapshot

        return out, blocked_symbols

    def _clone_barbell_plans(self, plans: dict[str, BarbellTargetPlan]) -> dict[str, BarbellTargetPlan]:
        return {symbol: replace(plan) for symbol, plan in plans.items()}

    def _composed_barbell_weight(self, plan: BarbellTargetPlan) -> float:
        return float(
            plan.core_target_weight
            + plan.overlay_target_weight
            + plan.convex_target_weight
            + plan.hedge_target_weight
            + plan.trend_sleeve_target_weight
            + plan.rates_curve_target_weight
            + plan.curve_rv_target_weight
            + plan.commodity_futures_target_weight
            + plan.crisis_trend_target_weight
            + plan.dollar_squeeze_target_weight
            + plan.reversal_target_weight
            + plan.melt_up_target_weight
        )

    def _refresh_composed_barbell_weight(self, plan: BarbellTargetPlan) -> None:
        plan.target_weight = self._composed_barbell_weight(plan)

    def _rates_rolling_ev_key(self, symbol: str, reason: str) -> str:
        direction = "LONG" if "DURATION_LONG" in reason else "SHORT"
        if symbol in RATE_YIELD_INSTRUMENTS:
            instrument = "YIELD"
        elif symbol in self.rates_curve_symbols:
            instrument = "FUTURE"
        else:
            instrument = "PROXY"
        return f"{symbol.upper()}|{direction}|{instrument}"

    def _update_rates_rolling_ev_history(self, market_event: MarketEvent) -> None:
        if not self.enable_rates_rolling_ev_gate_layer:
            return
        while self._rates_rolling_ev_pending:
            pending = self._rates_rolling_ev_pending[0]
            if int(pending["due_bar"]) > self._bar_index:
                break
            self._rates_rolling_ev_pending.popleft()
            symbol = str(pending["symbol"])
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            entry_price = float(pending["entry_price"])
            if entry_price <= 0:
                continue
            side = float(pending["side"])
            outcome = side * (float(bar.close) / entry_price - 1.0)
            key = str(pending["key"])
            history = self._rates_rolling_ev_history.setdefault(key, deque(maxlen=252))
            history.append(float(outcome))

    def _queue_rates_rolling_ev_candidate(
        self,
        *,
        symbol: str,
        reason: str,
        side: int,
        price: float,
    ) -> tuple[str, int, float | None]:
        key = self._rates_rolling_ev_key(symbol, reason)
        history = self._rates_rolling_ev_history.setdefault(key, deque(maxlen=252))
        obs_count = len(history)
        mean = float(np.mean(history)) if obs_count else None
        if price > 0:
            self._rates_rolling_ev_pending.append(
                {
                    "due_bar": self._bar_index + self.rates_rolling_ev_horizon,
                    "symbol": symbol.upper(),
                    "side": int(side),
                    "entry_price": float(price),
                    "key": key,
                }
            )
        return key, obs_count, mean

    def _rates_rolling_ev_gate_blocks(
        self,
        *,
        symbol: str,
        reason: str,
        side: int,
        price: float,
    ) -> bool:
        if not self.enable_rates_rolling_ev_gate_layer:
            return False
        _key, obs_count, mean = self._queue_rates_rolling_ev_candidate(
            symbol=symbol,
            reason=reason,
            side=side,
            price=price,
        )
        if mean is not None:
            self._last_rates_ev_gate_mean += mean
        if obs_count < self.rates_rolling_ev_min_obs:
            self._last_rates_ev_gate_pass_count += 1
            return False
        self._last_rates_ev_gate_ready_count += 1
        if mean is not None and mean <= self.rates_rolling_ev_min_mean:
            self._last_rates_ev_gate_block_count += 1
            return True
        self._last_rates_ev_gate_pass_count += 1
        return False

    def _standardized_price_move(
        self,
        symbol: str,
        *,
        move_lookback: int = 10,
        vol_lookback: int = 20,
    ) -> float | None:
        closes = np.fromiter(self._closes[symbol], dtype=float)
        if len(closes) < max(move_lookback + 1, vol_lookback + 1):
            return None
        start = float(closes[-move_lookback - 1])
        end = float(closes[-1])
        if start <= 0:
            return None
        returns = np.diff(closes[-(vol_lookback + 1) :]) / closes[-(vol_lookback + 1) : -1]
        vol = float(np.std(returns, ddof=0))
        if vol <= 1e-9:
            return None
        move = end / start - 1.0
        return float(move / (vol * np.sqrt(move_lookback)))

    def _realized_vol_zscore(
        self,
        symbol: str,
        *,
        vol_lookback: int = 21,
        z_lookback: int = 252,
    ) -> float | None:
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        if len(closes) < vol_lookback + z_lookback + 2:
            return None
        returns = np.diff(closes) / closes[:-1]
        vols = pd.Series(returns).rolling(vol_lookback, min_periods=vol_lookback).std(ddof=0).dropna()
        if len(vols) < z_lookback + 1:
            return None
        prior = vols.iloc[-z_lookback - 1 : -1].to_numpy(dtype=float)
        std = float(np.std(prior, ddof=0))
        if std <= 1e-9:
            return None
        return float((float(vols.iloc[-1]) - float(np.mean(prior))) / std)

    def _lookback_drawdown(self, symbol: str, lookback: int) -> float | None:
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        if len(closes) < lookback:
            return None
        recent = closes[-lookback:]
        peak = float(np.max(recent))
        if peak <= 0.0:
            return None
        return float(recent[-1] / peak - 1.0)

    def _time_series_trend_score(self, symbol: str) -> float | None:
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        if len(closes) < 127:
            return None
        score = 0.0
        weight_total = 0.0
        for lookback, weight in ((21, 0.25), (63, 0.35), (126, 0.40)):
            move = self._standardized_price_move(symbol, move_lookback=lookback, vol_lookback=max(21, lookback // 2))
            if move is None:
                continue
            score += weight * float(np.clip(move, -3.0, 3.0))
            weight_total += weight
        if weight_total <= 0.0:
            return None
        return float(score / weight_total)

    def _crisis_stress_score(self) -> float:
        stress = 0.0
        if bool(self._last_engine_b_feedback.get("a_stress", False)):
            stress += 0.35
        if bool(self._last_engine_b_feedback.get("emb_stress", False)):
            stress += 0.25
        hyg_drawdown = self._hyg_drawdown()
        if hyg_drawdown <= self.dollar_squeeze_hyg_drawdown:
            stress += min(abs(hyg_drawdown) / max(abs(self.dollar_squeeze_hyg_drawdown), 1e-9), 1.0) * 0.25
        if self._last_engine_b_extreme_active:
            stress += 0.25
        return float(min(stress, 1.0))

    def _engine_a_pnl_5d_zscore(self) -> float | None:
        history = np.fromiter(self._engine_a_pnl_5d_ratio_history, dtype=float)
        if len(history) < self.overlay_leader_min_obs:
            return None
        std = float(np.std(history, ddof=0))
        if std <= 1e-9:
            return None
        mean = float(np.mean(history))
        current = float(self._last_engine_b_feedback["engine_a_pnl_5d_ratio"])
        return float((current - mean) / std)

    def _overlay_leader_threshold(self, symbol: str) -> float:
        history = np.fromiter(self._overlay_leader_history.get(symbol, deque()), dtype=float)
        if len(history) < self.overlay_leader_min_obs:
            return self.overlay_leader_threshold_fallback
        quantile = float(np.quantile(history, self.overlay_leader_quantile))
        return float(
            min(
                max(quantile, self.overlay_leader_threshold_floor),
                self.overlay_leader_threshold_cap,
            )
        )

    def _apply_velocity_exits(self, plans: dict[str, BarbellTargetPlan]) -> dict[str, BarbellTargetPlan]:
        if self.portfolio is None:
            return self._clone_barbell_plans(plans)

        out = self._clone_barbell_plans(plans)
        for symbol in self.dislocation_symbols:
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B" or plan.target_weight == 0.0:
                continue
            qty = int(self.portfolio.position_for_symbol(symbol).quantity)
            if qty == 0 or np.sign(qty) != np.sign(plan.target_weight):
                continue
            state = self._engine_b_state.get(symbol)
            if state is None or int(state["sign"]) != (1 if qty > 0 else -1):
                continue
            if not self._velocity_decelerated(
                current_velocity=plan.velocity_signal,
                peak_velocity=float(state["peak_velocity"]),
                current_score=abs(plan.score),
                peak_score=float(state["peak_score"]),
            ):
                continue
            plan.target_weight = 0.0
            plan.core_target_weight = 0.0
            plan.overlay_target_weight = 0.0
            plan.convex_target_weight = 0.0
            plan.overlay_reason = ""
            plan.convex_reason = ""
            plan.reason = "ENGINE_B_VELOCITY_EXIT"
        return out

    def _apply_convex_dislocation_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_engine_b_convex_gross = 0.0
        self._last_engine_b_convex_count = 0
        if not self.enable_convex_dislocation_layer:
            return out

        remaining_gross = self.engine_b_convex_max_gross
        candidates: list[tuple[str, float, float, float]] = []
        for symbol in self.dislocation_symbols:
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B" or plan.core_target_weight == 0.0:
                continue
            if plan.core_phase < self.engine_b_convex_phase_floor:
                continue
            if not plan.extreme_regime and abs(plan.score) < self.engine_b_convex_trigger_score:
                continue
            if plan.coherence < self.engine_b_convex_coherence_floor:
                continue
            vol_z = self._realized_vol_zscore(symbol)
            if vol_z is not None and vol_z < self.engine_b_convex_vol_z_floor and not plan.extreme_regime:
                continue
            phase_scale = 0.60 if plan.core_phase == 2 else 1.0
            intensity = min(
                max(
                    (abs(plan.score) - self.engine_b_convex_trigger_score)
                    / max(self.engine_b_max_threshold - self.engine_b_convex_trigger_score, 1e-9),
                    0.0,
                ),
                1.0,
            )
            if plan.extreme_regime:
                intensity = max(intensity, 0.75)
            requested = self.engine_b_convex_symbol_weight * phase_scale * max(intensity, 0.25)
            available = max(self.engine_b_convex_max_symbol_weight - abs(plan.target_weight), 0.0)
            add_abs = min(requested, available)
            if add_abs <= 0.0:
                continue
            candidates.append((symbol, add_abs, abs(plan.score), float(vol_z or 0.0)))

        candidates.sort(key=lambda item: (item[2], item[3]), reverse=True)
        for symbol, add_abs, _, _ in candidates:
            if remaining_gross <= 1e-9:
                break
            plan = out[symbol]
            add_abs = min(add_abs, remaining_gross)
            sign = 1 if plan.core_target_weight > 0.0 else -1
            signed_add = sign * add_abs
            plan.convex_target_weight = signed_add
            plan.convex_reason = "ENGINE_B_CONVEX_PROXY"
            self._refresh_composed_barbell_weight(plan)
            remaining_gross -= add_abs
            self._last_engine_b_convex_gross += float(add_abs)
            self._last_engine_b_convex_count += 1
        return out

    def _apply_conditional_hedge_overlay(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        region_states: dict[str, dict[str, float]],
        market_event: MarketEvent,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_conditional_hedge_gross = 0.0
        self._last_conditional_hedge_count = 0
        if not self.enable_conditional_hedge_layer or not self.hedge_symbols:
            return out

        core_plans = [
            plan
            for plan in out.values()
            if plan.engine == "ENGINE_B" and abs(plan.core_target_weight) > 0.0
        ]
        core_gross = float(sum(abs(plan.core_target_weight) for plan in core_plans))
        if core_gross < self.hedge_core_gross_threshold:
            return out

        factor_names = (
            "factor_equity",
            "factor_duration",
            "factor_credit",
            "factor_commodity",
            "factor_dollar",
            "factor_gold",
            "factor_inflation",
        )
        net_factor = {name: 0.0 for name in factor_names}
        for plan in core_plans:
            exposures = self._factor_exposures(symbol=plan.symbol, asset_class=plan.asset_class)
            for name in factor_names:
                net_factor[name] += plan.core_target_weight * float(exposures.get(name, 0.0))

        us_state = region_states.get("US", {})
        growth_z = float(us_state.get("growth", 0.0))
        inflation_z = float(us_state.get("inflation", 0.0))
        liquidity_z = float(us_state.get("liquidity", 0.0))
        credit_z = float(us_state.get("credit", 0.0))
        risk_off = max(-growth_z, -liquidity_z, -credit_z, 0.0)
        inflation_pressure = max(inflation_z, 0.0)

        candidates: list[tuple[str, float, float, str]] = []
        for symbol in self.hedge_symbols:
            if symbol not in market_event.bars:
                continue
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            if len(closes) < 105:
                continue
            trend = _sma(closes, self._trend_period_for_symbol(symbol=symbol, asset_class="HEDGE", sign=1))
            if trend is None:
                continue
            close = float(market_event.bars[symbol].close)
            if close <= trend:
                continue

            score = 0.0
            reason = "CONDITIONAL_HEDGE"
            if symbol in {"TLT", "IEF"}:
                # Duration is useful only when growth/credit stress dominates inflation pressure.
                score = risk_off - 0.50 * inflation_pressure
                if abs(net_factor["factor_equity"]) < 0.20 and abs(net_factor["factor_credit"]) < 0.15:
                    score *= 0.50
                reason = "CONDITIONAL_DURATION_HEDGE"
            elif symbol == "GLD":
                score = 0.70 * inflation_pressure + 0.30 * max(-liquidity_z, 0.0)
                if abs(net_factor["factor_commodity"]) < 0.15 and abs(net_factor["factor_equity"]) < 0.20:
                    score *= 0.50
                reason = "CONDITIONAL_GOLD_HEDGE"
            elif symbol == "TIP":
                score = inflation_pressure - 0.35 * max(float(us_state.get("policy", 0.0)), 0.0)
                if abs(net_factor["factor_inflation"]) < 0.15:
                    score *= 0.50
                reason = "CONDITIONAL_TIPS_HEDGE"

            if score < self.hedge_min_score:
                continue
            candidates.append((symbol, score, min(self.hedge_max_symbol_weight, self.hedge_max_gross), reason))

        if not candidates:
            return out

        candidates.sort(key=lambda item: item[1], reverse=True)
        remaining = self.hedge_max_gross
        for symbol, score, requested, reason in candidates:
            if remaining <= 1e-9:
                break
            add_weight = min(requested, remaining)
            plan = out.get(symbol)
            if plan is None:
                continue
            plan.engine = "HEDGE" if plan.engine == "UNASSIGNED" else plan.engine
            plan.score = max(plan.score, score)
            plan.theme = "CONDITIONAL_HEDGE"
            plan.reason = reason
            plan.hedge_target_weight = add_weight
            plan.hedge_reason = reason
            self._refresh_composed_barbell_weight(plan)
            remaining -= add_weight
            self._last_conditional_hedge_gross += float(abs(add_weight))
            self._last_conditional_hedge_count += 1
        return out

    def _trend_sleeve_score(self, symbol: str) -> tuple[float, float] | None:
        closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
        max_lookback = max(self.trend_sleeve_lookbacks)
        if len(closes) < max(max_lookback + 1, self.trend_sleeve_vol_lookback + 1):
            return None

        votes: list[float] = []
        for lookback in self.trend_sleeve_lookbacks:
            start = float(closes[-lookback - 1])
            end = float(closes[-1])
            if start <= 0.0:
                continue
            ret = end / start - 1.0
            if ret > 0.0:
                votes.append(1.0)
            elif ret < 0.0:
                votes.append(-1.0)
        if len(votes) < 3:
            return None

        returns = np.diff(closes[-(self.trend_sleeve_vol_lookback + 1) :]) / closes[
            -(self.trend_sleeve_vol_lookback + 1) : -1
        ]
        vol = float(np.std(returns, ddof=0) * np.sqrt(252.0)) if len(returns) else 0.0
        if vol <= 0.0:
            return None
        return float(np.mean(votes)), vol

    def _apply_time_series_momentum_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        as_of: date,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_trend_sleeve_gross = 0.0
        self._last_trend_sleeve_count = 0
        if not self.enable_time_series_momentum_layer or not self.trend_sleeve_symbols:
            return out

        core_gross = float(
            sum(abs(plan.core_target_weight) for plan in out.values() if plan.engine == "ENGINE_B")
        )
        if core_gross >= self.trend_sleeve_active_core_gross:
            self._trend_sleeve_weights = {}
            self._last_trend_sleeve_rebalance = (as_of.year, as_of.month)
            return out

        marker = (as_of.year, as_of.month)
        rebalance = marker != self._last_trend_sleeve_rebalance
        if not rebalance:
            for symbol, signed_weight in self._trend_sleeve_weights.items():
                plan = out.get(symbol)
                if plan is None:
                    continue
                if plan.core_target_weight != 0.0 and np.sign(plan.core_target_weight) != np.sign(signed_weight):
                    continue
                plan.engine = "TREND_SLEEVE" if plan.engine == "UNASSIGNED" else plan.engine
                plan.theme = "TIME_SERIES_MOMENTUM" if plan.theme in {"NONE", "CONDITIONAL_HEDGE"} else plan.theme
                plan.reason = "TIME_SERIES_MOMENTUM"
                plan.trend_sleeve_target_weight = signed_weight
                plan.trend_sleeve_reason = "TIME_SERIES_MOMENTUM"
                self._refresh_composed_barbell_weight(plan)
                self._last_trend_sleeve_gross += float(abs(signed_weight))
                self._last_trend_sleeve_count += 1
            return out

        gross_budget = self.trend_sleeve_max_gross
        if gross_budget <= 0.0:
            return out

        candidates: list[tuple[str, float, float]] = []
        for symbol in self.trend_sleeve_symbols:
            plan = out.get(symbol)
            if plan is None:
                continue
            scored = self._trend_sleeve_score(symbol)
            if scored is None:
                continue
            score, vol = scored
            if abs(score) < self.trend_sleeve_min_abs_score:
                continue
            signed_weight = 1.0 if score > 0.0 else -1.0
            if plan.core_target_weight != 0.0 and np.sign(plan.core_target_weight) != signed_weight:
                continue
            priority = abs(score) / max(vol, 1e-9)
            candidates.append((symbol, score, priority))

        if not candidates:
            self._last_trend_sleeve_rebalance = marker
            self._trend_sleeve_weights = {}
            return out

        candidates.sort(key=lambda item: item[2], reverse=True)
        denom = max(sum(priority for _, _, priority in candidates), 1e-9)
        next_weights: dict[str, float] = {}
        for symbol, score, priority in candidates:
            plan = out[symbol]
            abs_weight = min(gross_budget * priority / denom, self.trend_sleeve_max_symbol_weight)
            if abs_weight <= 0.0:
                continue
            signed_weight = abs_weight if score > 0.0 else -abs_weight
            plan.engine = "TREND_SLEEVE" if plan.engine == "UNASSIGNED" else plan.engine
            plan.score = score if plan.score == 0.0 else plan.score
            plan.theme = "TIME_SERIES_MOMENTUM" if plan.theme in {"NONE", "CONDITIONAL_HEDGE"} else plan.theme
            plan.reason = "TIME_SERIES_MOMENTUM"
            plan.trend_sleeve_target_weight = signed_weight
            plan.trend_sleeve_reason = "TIME_SERIES_MOMENTUM"
            self._refresh_composed_barbell_weight(plan)
            next_weights[symbol] = signed_weight
            self._last_trend_sleeve_gross += float(abs(signed_weight))
            self._last_trend_sleeve_count += 1
        self._last_trend_sleeve_rebalance = marker
        self._trend_sleeve_weights = next_weights
        return out

    def _apply_rates_curve_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        region_states: dict[str, dict[str, float]],
        market_event: MarketEvent,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_rates_curve_gross = 0.0
        self._last_rates_curve_count = 0
        self._last_rates_curve_futures_gross = 0.0
        self._last_rates_curve_proxy_gross = 0.0
        self._last_rates_curve_futures_count = 0
        self._last_rates_curve_proxy_count = 0
        self._last_rates_curve_zero_contract_fallback_count = 0
        self._last_rates_curve_missing_market_count = 0
        self._last_rates_curve_trend_filter_count = 0
        self._last_rates_ev_gate_block_count = 0
        self._last_rates_ev_gate_pass_count = 0
        self._last_rates_ev_gate_ready_count = 0
        self._last_rates_ev_gate_mean = 0.0
        self._update_rates_rolling_ev_history(market_event)
        if not self.enable_rates_curve_layer or not (self.rates_curve_symbols or self.rates_proxy_symbols):
            return out

        if self.enable_market_evidence_layer:
            duration_evidence = self._market_duration_evidence_score()
            if duration_evidence is None:
                return out
            direction_score = float(duration_evidence)
            sign = 1 if direction_score > 0.0 else -1
            entry_score = self.market_evidence_rates_entry_score
        else:
            us = region_states.get("US", {})
            growth = float(us.get("growth", 0.0))
            inflation = float(us.get("inflation", 0.0))
            policy = float(us.get("policy", 0.0))
            credit = float(us.get("credit", 0.0))
            liquidity = float(us.get("liquidity", 0.0))

            recession_duration = max(-growth, -credit, -liquidity, 0.0) - 0.60 * max(inflation, policy, 0.0)
            inflation_duration_short = max(inflation, policy, 0.0) - 0.35 * max(-growth, -credit, 0.0)
            direction_score = (
                recession_duration
                if recession_duration >= inflation_duration_short
                else -inflation_duration_short
            )
            sign = 1 if direction_score > 0.0 else -1
            entry_score = (
                self.rates_curve_duration_long_entry_score
                if sign > 0
                else self.rates_curve_duration_short_entry_score
            )
        if abs(direction_score) < entry_score:
            return out

        intensity = min(abs(direction_score) / 2.5, 1.0)
        direction_budget_mult = self.rates_curve_duration_long_gross_mult if sign > 0 else 1.0
        gross_budget = self.rates_curve_max_gross * direction_budget_mult * max(intensity, 0.35)

        # Use the 10Y future as the main policy/growth expression, and add 30Y only
        # when the signal is recessionary duration rather than inflation short-duration.
        weights: dict[str, float] = {}
        def first_available(candidates: tuple[str, ...]) -> str | None:
            for candidate in candidates:
                if candidate in self.rates_curve_symbols:
                    return candidate
            return None

        main_candidates = (
            ("ZN=F", "ZF=F", "10Y=F", "^TNX", "5YY=F", "^FVX")
            if sign > 0
            else ("10Y=F", "^TNX", "ZN=F", "5YY=F", "^FVX", "ZF=F")
        )
        main_tenor = first_available(main_candidates)
        if main_tenor is not None:
            weights[main_tenor] = 0.65
        if sign > 0:
            long_tenor = first_available(("30Y=F", "ZB=F"))
            if long_tenor is not None:
                weights[long_tenor] = 0.35
        elif sign < 0:
            short_tenor = first_available(("2YY=F", "^FVX", "ZT=F", "ZF=F"))
            if short_tenor is not None:
                weights[short_tenor] = 0.35
        if not weights:
            return out

        def fallback_symbol(symbol: str) -> str | None:
            # Walk the whole expression chain so a missing yield future can still
            # resolve through a yield-index proxy, standard future, then ETF proxy.
            fallback_chains = {
                "2YY=F": ("^FVX", "ZT=F", "ZF=F", "IEF"),
                "5YY=F": ("^FVX", "ZF=F", "IEF"),
                "^FVX": ("ZF=F", "IEF"),
                "10Y=F": ("^TNX", "ZN=F", "IEF"),
                "^TNX": ("ZN=F", "IEF"),
                "30Y=F": ("ZB=F", "TLT", "^TYX"),
                "^TYX": ("ZB=F", "TLT"),
                "ZT=F": ("IEF",),
                "ZF=F": ("IEF",),
                "ZN=F": ("IEF",),
                "ZB=F": ("TLT",),
            }
            for candidate in fallback_chains.get(symbol, ()):
                if candidate in self.rates_curve_symbols and candidate in market_event.bars:
                    return candidate
                if candidate in self.rates_proxy_symbols and candidate in market_event.bars:
                    return candidate
            if "IEF" in self.rates_proxy_symbols and "IEF" in market_event.bars:
                return "IEF"
            if "TLT" in self.rates_proxy_symbols and "TLT" in market_event.bars:
                return "TLT"
            return None

        def rates_reason_suffix(symbol: str) -> str:
            return "_STANDARD" if symbol in self.rates_curve_symbols else "_PROXY"

        def signed_weight_for_symbol(symbol: str, duration_sign: int, abs_weight: float) -> float:
            # Yield futures are priced in yield, so their trading direction is
            # inverted relative to duration/bond-price futures and ETF proxies.
            instrument_sign = -duration_sign if symbol in RATE_YIELD_INSTRUMENTS else duration_sign
            return instrument_sign * abs_weight

        equity = max(float(self.portfolio.latest_equity), 1e-9) if self.portfolio is not None else 0.0
        denom = max(sum(weights.values()), 1e-9)
        for symbol, share in weights.items():
            target_symbol = symbol
            reason_suffix = ""
            abs_requested_weight = min(gross_budget * share / denom, self.rates_curve_max_symbol_weight)
            raw_signed_weight = signed_weight_for_symbol(symbol, sign, abs_requested_weight)
            if raw_signed_weight == 0.0:
                continue
            if symbol not in market_event.bars:
                self._last_rates_curve_missing_market_count += 1
                proxy = fallback_symbol(symbol)
                if proxy is None or proxy not in market_event.bars:
                    continue
                target_symbol = proxy
                raw_signed_weight = signed_weight_for_symbol(target_symbol, sign, abs(raw_signed_weight))
                reason_suffix = rates_reason_suffix(target_symbol)
            else:
                target_contracts = _weight_target_size(
                    equity=equity,
                    price=float(market_event.bars[symbol].close) * self._instrument_multiplier(symbol),
                    target_weight=raw_signed_weight,
                )
                if abs(target_contracts) < 1:
                    self._last_rates_curve_zero_contract_fallback_count += 1
                    proxy = fallback_symbol(symbol)
                    if proxy is None or proxy not in market_event.bars:
                        continue
                    target_symbol = proxy
                    raw_signed_weight = signed_weight_for_symbol(target_symbol, sign, abs(raw_signed_weight))
                    reason_suffix = rates_reason_suffix(target_symbol)
            if target_symbol in self.rates_curve_symbols and target_symbol in market_event.bars:
                target_contracts = _weight_target_size(
                    equity=equity,
                    price=float(market_event.bars[target_symbol].close) * self._instrument_multiplier(target_symbol),
                    target_weight=raw_signed_weight,
                )
                if abs(target_contracts) < 1:
                    self._last_rates_curve_zero_contract_fallback_count += 1
                    proxy = fallback_symbol(target_symbol)
                    if proxy is None or proxy not in market_event.bars:
                        continue
                    target_symbol = proxy
                    raw_signed_weight = signed_weight_for_symbol(target_symbol, sign, abs(raw_signed_weight))
                    reason_suffix = rates_reason_suffix(target_symbol)
            trend_symbol = symbol if symbol in market_event.bars else target_symbol
            closes = np.fromiter(self._closes.get(trend_symbol, deque()), dtype=float)
            if len(closes) >= 55:
                trend = _sma(closes, 50)
                if trend is not None:
                    close = float(market_event.bars[trend_symbol].close)
                    trend_sign = 1 if raw_signed_weight > 0.0 else -1
                    if trend_sign > 0 and close < trend:
                        self._last_rates_curve_trend_filter_count += 1
                        continue
                    if trend_sign < 0 and close > trend:
                        self._last_rates_curve_trend_filter_count += 1
                        continue
            plan = out.get(target_symbol)
            if plan is None:
                continue
            signed_weight = raw_signed_weight
            if target_symbol != symbol and target_symbol not in self.rates_curve_symbols:
                signed_weight = sign * min(abs(raw_signed_weight), self.rates_curve_max_symbol_weight * 0.75)
            if abs(signed_weight) <= 0.0:
                continue
            yield_suffix = "_YIELD" if target_symbol in RATE_YIELD_INSTRUMENTS else ""
            reason = (
                "RATES_CURVE_DURATION_LONG" if sign > 0 else "RATES_CURVE_DURATION_SHORT"
            ) + yield_suffix + reason_suffix
            if self._rates_rolling_ev_gate_blocks(
                symbol=target_symbol,
                reason=reason,
                side=1 if signed_weight > 0.0 else -1,
                price=float(market_event.bars[target_symbol].close),
            ):
                continue
            plan.engine = "RATES_CURVE" if plan.engine == "UNASSIGNED" else plan.engine
            plan.score = direction_score if plan.score == 0.0 else plan.score
            plan.theme = "RATES_CURVE"
            plan.reason = reason
            plan.rates_curve_target_weight += signed_weight
            plan.rates_curve_reason = plan.reason
            self._refresh_composed_barbell_weight(plan)
            self._last_rates_curve_gross += abs(float(signed_weight))
            self._last_rates_curve_count += 1
            if target_symbol == symbol or target_symbol in self.rates_curve_symbols:
                self._last_rates_curve_futures_gross += abs(float(signed_weight))
                self._last_rates_curve_futures_count += 1
            else:
                self._last_rates_curve_proxy_gross += abs(float(signed_weight))
                self._last_rates_curve_proxy_count += 1
        return out

    def _curve_rv_slope_state(self) -> dict[str, float] | None:
        front_closes = np.fromiter(self._closes.get("^FVX", deque()), dtype=float)
        back_closes = np.fromiter(self._closes.get("^TNX", deque()), dtype=float)
        n = min(len(front_closes), len(back_closes))
        if n < 253:
            return None
        front = front_closes[-n:]
        back = back_closes[-n:]
        slope = back - front
        current = float(slope[-1])
        prior = slope[-253:-1]
        std = float(np.std(prior, ddof=0))
        if std <= 1e-9:
            return None
        slope_z = float((current - float(np.mean(prior))) / std)
        if len(slope) >= 85:
            diff_std = float(np.std(np.diff(slope[-85:-21]), ddof=0))
        else:
            diff_std = float(np.std(np.diff(slope[-64:]), ddof=0))
        if diff_std <= 1e-9 or len(slope) < 22:
            slope_momentum = 0.0
        else:
            slope_momentum = float((current - float(slope[-22])) / (diff_std * np.sqrt(21.0)))
        sma50 = float(np.mean(slope[-50:])) if len(slope) >= 50 else current
        return {
            "slope": current,
            "slope_z": slope_z,
            "slope_momentum": slope_momentum,
            "sma50": sma50,
        }

    def _apply_curve_rv_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        region_states: dict[str, dict[str, float]],
        market_event: MarketEvent,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_curve_rv_gross = 0.0
        self._last_curve_rv_count = 0
        self._last_curve_rv_futures_gross = 0.0
        self._last_curve_rv_proxy_gross = 0.0
        self._last_curve_rv_futures_count = 0
        self._last_curve_rv_proxy_count = 0
        self._last_curve_rv_slope_z = 0.0
        self._last_curve_rv_slope_momentum = 0.0
        self._last_curve_rv_regime_score = 0.0
        if not self.enable_curve_rv_layer or self._engine_a_shutoff_active:
            return out

        slope_state = self._curve_rv_slope_state()
        if slope_state is None:
            return out
        slope_z = float(slope_state["slope_z"])
        slope_momentum = float(slope_state["slope_momentum"])
        slope = float(slope_state["slope"])
        slope_sma50 = float(slope_state["sma50"])
        self._last_curve_rv_slope_z = slope_z
        self._last_curve_rv_slope_momentum = slope_momentum

        us = region_states.get("US", {})
        growth = float(us.get("growth", 0.0))
        inflation = float(us.get("inflation", 0.0))
        policy = float(us.get("policy", 0.0))
        rate_cut_score = max(-policy, 0.0) + max(-growth, 0.0)
        rate_hike_score = max(policy, 0.0) + max(inflation, 0.0)

        steepener_sign = 0
        regime_score = 0.0
        reason = ""
        if (
            rate_cut_score >= self.curve_rv_regime_entry_score
            and slope_z <= -self.curve_rv_slope_z_entry
            and (slope_momentum > 0.0 or slope > slope_sma50)
        ):
            # Bull steepener: front-end duration rallies more than 10Y duration.
            steepener_sign = 1
            regime_score = rate_cut_score
            reason = "CURVE_RV_BULL_STEEPENER"
        elif (
            rate_hike_score >= self.curve_rv_regime_entry_score
            and slope_z >= self.curve_rv_slope_z_entry
            and (slope_momentum < 0.0 or slope < slope_sma50)
        ):
            # Bear flattener: front-end duration sells off more than 10Y duration.
            steepener_sign = -1
            regime_score = rate_hike_score
            reason = "CURVE_RV_BEAR_FLATTENER"
        if steepener_sign == 0:
            return out
        self._last_curve_rv_regime_score = float(regime_score)

        intensity = min(max(abs(slope_z), abs(slope_momentum), regime_score) / 2.5, 1.0)
        gross_budget = self.curve_rv_max_gross * max(intensity, 0.35)
        leg_weight = min(gross_budget / 2.0, self.curve_rv_max_leg_weight)
        if leg_weight <= 0.0:
            return out

        futures_legs = {"ZF=F": steepener_sign, "ZN=F": -steepener_sign}
        proxy_legs = {"^FVX": -steepener_sign, "^TNX": steepener_sign}

        equity = max(float(self.portfolio.latest_equity), 1e-9) if self.portfolio is not None else 0.0

        def tradable_legs(legs: dict[str, int]) -> bool:
            for symbol, sign in legs.items():
                bar = market_event.bars.get(symbol)
                if bar is None or symbol not in out:
                    return False
                target_qty = _weight_target_size(
                    equity=equity,
                    price=float(bar.close) * self._instrument_multiplier(symbol),
                    target_weight=float(sign) * leg_weight,
                )
                if abs(target_qty) < 1:
                    return False
            return True

        if tradable_legs(futures_legs):
            legs = futures_legs
            instrument_label = "FUTURES"
        elif tradable_legs(proxy_legs):
            legs = proxy_legs
            instrument_label = "YIELD_PROXY"
        else:
            return out

        for symbol, sign in legs.items():
            plan = out.get(symbol)
            bar = market_event.bars.get(symbol)
            if plan is None or bar is None:
                continue
            signed_weight = float(sign) * leg_weight
            plan.engine = "CURVE_RV" if plan.engine == "UNASSIGNED" else plan.engine
            if abs(regime_score) > abs(plan.score):
                plan.score = float(steepener_sign) * float(regime_score)
            plan.theme = "CURVE_RV"
            plan.reason = f"{reason}_{instrument_label}"
            plan.curve_rv_target_weight += signed_weight
            plan.curve_rv_reason = plan.reason
            self._refresh_composed_barbell_weight(plan)
            self._last_curve_rv_gross += abs(signed_weight)
            self._last_curve_rv_count += 1
            if instrument_label == "FUTURES":
                self._last_curve_rv_futures_gross += abs(signed_weight)
                self._last_curve_rv_futures_count += 1
            else:
                self._last_curve_rv_proxy_gross += abs(signed_weight)
                self._last_curve_rv_proxy_count += 1
        return out

    def _apply_commodity_futures_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        region_states: dict[str, dict[str, float]],
        market_event: MarketEvent,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_commodity_futures_gross = 0.0
        self._last_commodity_futures_count = 0
        self._last_commodity_futures_reject_counts = {
            "missing_curve": 0,
            "stale_curve": 0,
            "weak_curve": 0,
            "low_liquidity": 0,
            "weak_macro": 0,
            "weak_momentum": 0,
            "momentum_mismatch": 0,
            "carry_mismatch": 0,
            "price_only": 0,
        }
        if (
            not self.enable_commodity_futures_sleeve_layer
            or not self.commodity_futures_symbols
        ):
            return out

        us = region_states.get("US", {})
        growth = float(us.get("growth", 0.0))
        inflation = float(us.get("inflation", 0.0))
        policy = float(us.get("policy", 0.0))
        credit = float(us.get("credit", 0.0))
        liquidity = float(us.get("liquidity", 0.0))
        stress = max(-growth, -credit, -liquidity, 0.0)

        as_of = market_event.timestamp.date()
        candidates: list[tuple[str, float, str, float, bool, float]] = []
        for symbol in self.commodity_futures_symbols:
            if symbol not in market_event.bars:
                continue
            curve = self.futures_curve_source.snapshot(symbol, as_of) if self.futures_curve_source is not None else None
            price_only = False
            curve_carry = 0.0
            if curve is None:
                # If real term-structure data is unavailable, admit only the
                # empirically useful energy/copper contracts under stricter
                # macro+momentum confirmation. This keeps it a satellite sleeve.
                if symbol not in {"CL=F", "HG=F"}:
                    self._last_commodity_futures_reject_counts["missing_curve"] += 1
                    continue
                price_only = True
                self._last_commodity_futures_reject_counts["price_only"] += 1
            else:
                if (as_of - curve.tradable_from).days > 10:
                    self._last_commodity_futures_reject_counts["stale_curve"] += 1
                    continue
                curve_carry = 0.70 * float(curve.carry_score) + 0.30 * float(curve.roll_yield_3m)
                if curve.liquidity_score < 0.50:
                    self._last_commodity_futures_reject_counts["low_liquidity"] += 1
                    continue
                if abs(curve_carry) < 0.02:
                    self._last_commodity_futures_reject_counts["weak_curve"] += 1
                    continue
            market_evidence = self._market_evidence_state(symbol) if self.enable_market_evidence_layer else None
            if self.enable_market_evidence_layer and market_evidence is None:
                self._last_commodity_futures_reject_counts["weak_macro"] += 1
                continue
            if market_evidence is not None:
                score = float(market_evidence["score"])
                daily_vol = float(market_evidence["daily_vol"])
                reason = "COMMODITY_FUTURES_MARKET_EVIDENCE"
            elif symbol == "GC=F":
                score = inflation - 0.60 * policy + 0.35 * stress - 0.20 * growth
                daily_vol = 0.0
                reason = "COMMODITY_FUTURES_GOLD_STRESS"
            elif symbol == "CL=F":
                score = inflation + 0.45 * growth + 0.20 * credit - 0.35 * policy - 0.25 * stress
                daily_vol = 0.0
                reason = "COMMODITY_FUTURES_ENERGY"
            elif symbol == "HG=F":
                score = 0.75 * growth + 0.45 * inflation + 0.25 * liquidity - 0.35 * policy - 0.35 * stress
                daily_vol = 0.0
                reason = "COMMODITY_FUTURES_CYCLICAL_METALS"
            else:
                score = inflation + 0.25 * growth - 0.25 * policy
                daily_vol = 0.0
                reason = "COMMODITY_FUTURES"

            move_63 = self._standardized_price_move(symbol, move_lookback=63, vol_lookback=63)
            move_21 = self._standardized_price_move(symbol, move_lookback=21, vol_lookback=42)
            momentum = 0.0
            if move_63 is not None:
                momentum += 0.65 * float(move_63)
            if move_21 is not None:
                momentum += 0.35 * float(move_21)
            if self.enable_market_evidence_layer:
                entry_score = self.market_evidence_commodity_entry_score
                min_momentum = self.market_evidence_commodity_momentum_floor
            else:
                entry_score = (
                    self.commodity_futures_price_only_entry_score
                    if price_only
                    else self.commodity_futures_entry_score
                )
                min_momentum = (
                    self.commodity_futures_price_only_min_momentum
                    if price_only
                    else self.commodity_futures_min_momentum
                )
            if abs(score) < entry_score:
                self._last_commodity_futures_reject_counts["weak_macro"] += 1
                continue
            if abs(momentum) < min_momentum:
                self._last_commodity_futures_reject_counts["weak_momentum"] += 1
                continue
            if np.sign(score) != np.sign(momentum):
                self._last_commodity_futures_reject_counts["momentum_mismatch"] += 1
                continue
            # Futures get admitted only when curve carry agrees with the macro/trend direction.
            if not price_only and np.sign(score) != np.sign(curve_carry):
                self._last_commodity_futures_reject_counts["carry_mismatch"] += 1
                continue
            curve_boost = 1.0 if price_only else 1.0 + min(abs(curve_carry) * 2.0, 0.50)
            adjusted_score = float(score) * curve_boost
            reason_suffix = "PRICE_CONFIRMED" if price_only else "CURVE_CONFIRMED"
            candidates.append(
                (symbol, adjusted_score, f"{reason}_{reason_suffix}", float(curve_carry), price_only, daily_vol)
            )

        if not candidates:
            return out

        candidates.sort(key=lambda row: abs(row[1]), reverse=True)
        selected = candidates[:2]
        denom = max(sum(abs(score) for _, score, _, _, _, _ in selected), 1e-9)
        for symbol, score, reason, curve_carry, price_only, daily_vol in selected:
            sign = 1 if score > 0.0 else -1
            max_gross = (
                self.commodity_futures_price_only_max_gross
                if price_only
                else self.commodity_futures_max_gross
            )
            max_symbol_weight = (
                self.commodity_futures_price_only_max_symbol_weight
                if price_only
                else self.commodity_futures_max_symbol_weight
            )
            if not price_only and daily_vol > 0.0:
                annualized_vol = max(daily_vol * np.sqrt(252.0), self.commodity_curve_annual_vol_floor)
                max_symbol_weight = min(
                    max_symbol_weight,
                    self.commodity_curve_annual_risk_target / annualized_vol,
                )
            raw_weight = max_gross * abs(score) / denom
            signed_weight = sign * min(raw_weight, max_symbol_weight)
            plan = out.get(symbol)
            if plan is None:
                continue
            plan.engine = "COMMODITY_FUTURES" if plan.engine == "UNASSIGNED" else plan.engine
            plan.score = score if plan.score == 0.0 else plan.score
            plan.theme = "COMMODITY_FUTURES"
            plan.reason = reason
            plan.commodity_futures_target_weight = signed_weight
            plan.commodity_futures_reason = f"{reason}:carry={curve_carry:.4f}"
            self._refresh_composed_barbell_weight(plan)
            self._last_commodity_futures_gross += abs(float(signed_weight))
            self._last_commodity_futures_count += 1
        return out

    def _apply_crisis_trend_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_crisis_trend_gross = 0.0
        self._last_crisis_trend_count = 0
        if not self.enable_crisis_trend_layer:
            return out

        stress = self._crisis_stress_score()
        if stress < 0.25:
            return out
        gross_budget = self.crisis_trend_base_gross + stress * (
            self.crisis_trend_stress_gross - self.crisis_trend_base_gross
        )
        candidates: list[tuple[str, float]] = []
        for symbol in self.dislocation_symbols:
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            trend_score = self._time_series_trend_score(symbol)
            if trend_score is None or abs(trend_score) < self.crisis_trend_min_score:
                continue
            candidates.append((symbol, trend_score))

        candidates.sort(key=lambda item: abs(item[1]), reverse=True)
        selected = candidates[: self.crisis_trend_top_n]
        denom = max(sum(abs(score) for _, score in selected), 1e-9)
        for symbol, trend_score in selected:
            plan = out[symbol]
            raw_abs = gross_budget * abs(trend_score) / denom
            sign = 1 if trend_score > 0.0 else -1
            room = max(self.crisis_trend_max_symbol_weight - abs(plan.target_weight), 0.0)
            add_abs = min(raw_abs, room)
            if add_abs <= 0.0:
                continue
            plan.crisis_trend_target_weight = sign * add_abs
            plan.crisis_trend_reason = "CRISIS_TREND"
            self._refresh_composed_barbell_weight(plan)
            self._last_crisis_trend_gross += float(add_abs)
            self._last_crisis_trend_count += 1
        return out

    def _apply_dollar_squeeze_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_dollar_squeeze_gross = 0.0
        self._last_dollar_squeeze_count = 0
        if not self.enable_dollar_squeeze_layer:
            return out

        uup_z = self._standardized_price_move("UUP", move_lookback=10, vol_lookback=63) if "UUP" in self.symbols else None
        hyg_drawdown = self._hyg_drawdown()
        stress = self._crisis_stress_score()
        trigger_strength = 0.0
        if uup_z is not None and uup_z >= self.dollar_squeeze_trigger_z:
            trigger_strength = max(
                trigger_strength,
                min((uup_z - self.dollar_squeeze_trigger_z) / max(self.dollar_squeeze_trigger_z, 1e-9), 1.0),
            )
        if hyg_drawdown <= self.dollar_squeeze_hyg_drawdown:
            trigger_strength = max(
                trigger_strength,
                min(abs(hyg_drawdown) / max(abs(self.dollar_squeeze_hyg_drawdown), 1e-9), 1.0),
            )
        if bool(self._last_engine_b_feedback.get("emb_stress", False)):
            trigger_strength = max(trigger_strength, 0.65)
        if stress >= 0.50:
            trigger_strength = max(trigger_strength, stress)
        if trigger_strength > 0.0 and not (
            stress >= 0.35
            or hyg_drawdown <= -0.01
            or bool(self._last_engine_b_feedback.get("emb_stress", False))
            or (uup_z is not None and uup_z >= self.dollar_squeeze_trigger_z * 1.6)
        ):
            return out
        if trigger_strength <= 0.0:
            return out

        desired: dict[str, float] = {
            "UUP": 0.15,
            "EEM": -0.12,
            "DBC": -0.08,
            "USO": -0.08,
            "CL=F": -0.10,
            "HG=F": -0.08,
        }
        if hyg_drawdown <= self.dollar_squeeze_hyg_drawdown:
            desired["SPY"] = -0.10

        remaining = self.dollar_squeeze_max_gross
        for symbol, base_weight in desired.items():
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            signed_weight = base_weight * min(trigger_strength, 1.0)
            room = max(self.dollar_squeeze_max_symbol_weight - abs(plan.target_weight), 0.0)
            add_abs = min(abs(signed_weight), room, remaining)
            if add_abs <= 0.0:
                continue
            plan.dollar_squeeze_target_weight = (1 if signed_weight > 0 else -1) * add_abs
            plan.dollar_squeeze_reason = "DOLLAR_SQUEEZE"
            self._refresh_composed_barbell_weight(plan)
            remaining -= add_abs
            self._last_dollar_squeeze_gross += float(add_abs)
            self._last_dollar_squeeze_count += 1
            if remaining <= 1e-9:
                break
        return out

    def _apply_liquidation_reversal_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_reversal_gross = 0.0
        self._last_reversal_count = 0
        if not self.enable_liquidation_reversal_layer:
            return out

        candidates: list[tuple[str, float, float]] = []
        next_state: dict[str, dict[str, float | int]] = {}
        remaining = self.reversal_max_gross
        for symbol in self.dislocation_symbols:
            if symbol == "UUP":
                continue
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            lookback = max(21, self.reversal_confirmation_lookback + 2)
            if len(closes) < lookback:
                continue
            sma10 = _sma(closes, 10)
            if sma10 is None:
                continue
            prior = self._reversal_state.get(symbol)
            if prior is not None:
                entry_bar = int(prior.get("entry_bar", self._bar_index))
                entry_price = float(prior.get("entry_price", closes[-1]))
                weight = float(prior.get("weight", 0.0))
                bars_held = self._bar_index - entry_bar
                still_valid = (
                    bars_held <= self.reversal_max_hold_bars
                    and closes[-1] >= sma10
                    and closes[-1] >= entry_price * (1.0 - self.reversal_stop_pct)
                )
                if still_valid and weight > 0.0:
                    plan.reversal_target_weight = weight
                    plan.reversal_reason = "LIQUIDATION_REVERSAL_HOLD"
                    self._refresh_composed_barbell_weight(plan)
                    self._last_reversal_gross += abs(weight)
                    self._last_reversal_count += 1
                    remaining = max(remaining - abs(weight), 0.0)
                    next_state[symbol] = {
                        "entry_bar": entry_bar,
                        "entry_price": entry_price,
                        "weight": weight,
                    }
                    continue
            drawdown = self._lookback_drawdown(symbol, 21)
            if drawdown is None or drawdown > self.reversal_drawdown_threshold:
                continue
            prior_high = float(np.max(closes[-self.reversal_confirmation_lookback - 1 : -1]))
            confirmed = closes[-1] > prior_high or closes[-1] > sma10
            if not confirmed:
                continue
            impulse = abs(drawdown) * max(self._crisis_stress_score(), 0.35)
            candidates.append((symbol, impulse, drawdown))

        candidates.sort(key=lambda item: item[1], reverse=True)
        for symbol, impulse, _ in candidates[: self.reversal_top_n]:
            plan = out[symbol]
            intensity = min(max(impulse / abs(self.reversal_drawdown_threshold), 0.25), 1.0)
            room = max(self.reversal_max_symbol_weight - abs(plan.target_weight), 0.0)
            add_abs = min(self.reversal_max_symbol_weight * intensity, room, remaining)
            if add_abs <= 0.0:
                continue
            plan.reversal_target_weight = add_abs
            plan.reversal_reason = "LIQUIDATION_REVERSAL"
            self._refresh_composed_barbell_weight(plan)
            next_state[symbol] = {
                "entry_bar": self._bar_index,
                "entry_price": float(np.fromiter(self._closes[symbol], dtype=float)[-1]),
                "weight": add_abs,
            }
            remaining -= add_abs
            self._last_reversal_gross += float(add_abs)
            self._last_reversal_count += 1
            if remaining <= 1e-9:
                break
        self._reversal_state = next_state
        return out

    def _apply_melt_up_reversal_sleeve(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_melt_up_gross = 0.0
        self._last_melt_up_count = 0
        if not self.enable_melt_up_reversal_layer:
            return out

        candidates: list[tuple[str, float]] = []
        next_state: dict[str, dict[str, float | int]] = {}
        remaining = self.melt_up_max_gross
        max_coherence = max((plan.coherence for plan in out.values() if plan.engine == "ENGINE_B"), default=0.0)
        for symbol in self.dislocation_symbols:
            if symbol == "UUP":
                continue
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            if plan.theme not in {"STAGFLATION", "DISINFLATION"} and max_coherence < 0.65:
                continue
            if self._theme_expression_sign(symbol=symbol, asset_class=plan.asset_class, theme=plan.theme) >= 0:
                continue
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            if len(closes) < 22:
                continue
            sma10 = _sma(closes, 10)
            if sma10 is None:
                continue
            prior = self._melt_up_state.get(symbol)
            if prior is not None:
                entry_bar = int(prior.get("entry_bar", self._bar_index))
                entry_price = float(prior.get("entry_price", closes[-1]))
                weight = float(prior.get("weight", 0.0))
                bars_held = self._bar_index - entry_bar
                still_valid = (
                    bars_held <= self.melt_up_max_hold_bars
                    and closes[-1] <= sma10
                    and closes[-1] <= entry_price * (1.0 + self.melt_up_stop_pct)
                )
                if still_valid and weight < 0.0:
                    plan.melt_up_target_weight = weight
                    plan.melt_up_reason = "MELT_UP_REVERSAL_HOLD"
                    self._refresh_composed_barbell_weight(plan)
                    self._last_melt_up_gross += abs(weight)
                    self._last_melt_up_count += 1
                    remaining = max(remaining - abs(weight), 0.0)
                    next_state[symbol] = {
                        "entry_bar": entry_bar,
                        "entry_price": entry_price,
                        "weight": weight,
                    }
                    continue
            gain_21 = float(closes[-1] / max(closes[-22], 1e-9) - 1.0)
            if gain_21 < self.melt_up_threshold:
                continue
            if closes[-1] >= sma10:
                continue
            candidates.append((symbol, gain_21))

        candidates.sort(key=lambda item: item[1], reverse=True)
        for symbol, gain in candidates[: self.melt_up_top_n]:
            plan = out[symbol]
            intensity = min(max(gain / 0.30, 0.50), 1.0)
            room = max(self.melt_up_max_symbol_weight - abs(plan.target_weight), 0.0)
            add_abs = min(self.melt_up_max_symbol_weight * intensity, room, remaining)
            if add_abs <= 0.0:
                continue
            signed_add = -add_abs
            plan.melt_up_target_weight = signed_add
            plan.melt_up_reason = "MELT_UP_REVERSAL"
            self._refresh_composed_barbell_weight(plan)
            next_state[symbol] = {
                "entry_bar": self._bar_index,
                "entry_price": float(np.fromiter(self._closes[symbol], dtype=float)[-1]),
                "weight": signed_add,
            }
            remaining -= add_abs
            self._last_melt_up_gross += float(add_abs)
            self._last_melt_up_count += 1
            if remaining <= 1e-9:
                break
        self._melt_up_state = next_state
        return out

    def _apply_cross_asset_confirmation(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_cross_asset_boost = 1.0
        if not self.enable_cross_asset_confirmation_layer:
            return out

        families: set[str] = set()
        for plan in out.values():
            if plan.engine != "ENGINE_B" or plan.core_target_weight == 0.0:
                continue
            asset_class = plan.asset_class.upper()
            if asset_class in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY"}:
                bucket = "COMMODITY"
            elif asset_class in {"BOND", "DURATION", "INFLATION_LINKED_BOND"}:
                bucket = "DURATION"
            elif asset_class in {"EQUITY", "EQUITY_INDEX"}:
                bucket = "EQUITY"
            else:
                bucket = asset_class
            families.add(bucket)
        if len(families) >= 3:
            boost = self.cross_asset_boost_3
        elif len(families) >= 2:
            boost = self.cross_asset_boost_2
        else:
            boost = 1.0
        self._last_cross_asset_boost = float(boost)
        if boost == 1.0:
            return out
        for plan in out.values():
            if plan.engine != "ENGINE_B" or plan.core_target_weight == 0.0:
                continue
            plan.core_target_weight *= boost
            plan.target_weight *= boost
        return out

    def _portfolio_vol_scalar(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> float:
        self._last_portfolio_vol_estimate = 0.0
        if not self.enable_portfolio_vol_targeting_layer:
            return 1.0
        active_symbols = [symbol for symbol, plan in plans.items() if plan.target_weight != 0.0]
        if len(active_symbols) < 2:
            return 1.0
        returns: dict[str, np.ndarray] = {}
        min_len = self.vol_lookback
        for symbol in active_symbols:
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            if len(closes) < min_len + 1:
                continue
            values = closes[-(min_len + 1) :]
            returns[symbol] = np.diff(values) / values[:-1]
        if len(returns) < 2:
            return 1.0
        frame = pd.DataFrame(returns).dropna()
        if len(frame) < 30:
            return 1.0
        cov_63 = frame.cov().to_numpy(dtype=float) * 252.0
        weights = np.array([plans[symbol].target_weight for symbol in frame.columns], dtype=float)
        variance_63 = float(weights @ cov_63 @ weights)
        realized_vol = float(np.sqrt(max(variance_63, 0.0)))
        if len(frame) >= 21:
            cov_21 = frame.tail(21).cov().to_numpy(dtype=float) * 252.0
            variance_21 = float(weights @ cov_21 @ weights)
            realized_vol = max(realized_vol, 1.2 * float(np.sqrt(max(variance_21, 0.0))))
        self._last_portfolio_vol_estimate = realized_vol
        if realized_vol <= 1e-8:
            return 1.0
        return float(np.clip(self.target_portfolio_vol / realized_vol, self.vol_scale_min, self.vol_scale_max))

    def _drawdown_throttle_scalar(self, *, equity: float) -> float:
        if not self.enable_drawdown_throttle_layer:
            return 1.0
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity <= 0.0:
            return 1.0
        drawdown = equity / self._peak_equity - 1.0
        if drawdown >= -0.07:
            return 1.0
        if drawdown >= -0.12:
            return 0.85
        if drawdown >= -0.18:
            return 0.70
        if drawdown >= -0.25:
            return 0.55
        return 0.40

    def _scale_barbell_plan(self, plan: BarbellTargetPlan, scalar: float) -> None:
        plan.target_weight *= scalar
        plan.core_target_weight *= scalar
        plan.overlay_target_weight *= scalar
        plan.convex_target_weight *= scalar
        plan.hedge_target_weight *= scalar
        plan.trend_sleeve_target_weight *= scalar
        plan.rates_curve_target_weight *= scalar
        plan.curve_rv_target_weight *= scalar
        plan.commodity_futures_target_weight *= scalar
        plan.crisis_trend_target_weight *= scalar
        plan.dollar_squeeze_target_weight *= scalar
        plan.reversal_target_weight *= scalar
        plan.melt_up_target_weight *= scalar
        plan.reallocation_target_weight *= scalar

    def _apply_portfolio_scalars(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        equity: float,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        vol_scalar = self._portfolio_vol_scalar(plans=out)
        dd_scalar = self._drawdown_throttle_scalar(equity=equity)
        scalar = vol_scalar * dd_scalar
        self._last_vol_target_scalar = float(vol_scalar)
        self._last_drawdown_throttle_scalar = float(dd_scalar)
        self._last_portfolio_scalar = float(scalar)
        if abs(scalar - 1.0) <= 1e-9:
            return out
        for plan in out.values():
            self._scale_barbell_plan(plan, scalar)
        return out

    def _apply_nav_stops(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        market_event: MarketEvent,
        equity: float,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_nav_stop_count = 0
        if not self.enable_nav_stop_layer or self.portfolio is None or equity <= 0.0:
            return out
        for symbol in self.dislocation_symbols:
            plan = out.get(symbol)
            bar = market_event.bars.get(symbol)
            if plan is None or bar is None:
                continue
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity == 0 or position.avg_price <= 0:
                continue
            pnl = (
                float(position.quantity)
                * (float(bar.close) - float(position.avg_price))
                * self._instrument_multiplier(symbol)
            )
            if pnl / equity > -self.position_nav_stop:
                continue
            plan.target_weight = 0.0
            plan.core_target_weight = 0.0
            plan.overlay_target_weight = 0.0
            plan.convex_target_weight = 0.0
            plan.hedge_target_weight = 0.0
            plan.trend_sleeve_target_weight = 0.0
            plan.rates_curve_target_weight = 0.0
            plan.curve_rv_target_weight = 0.0
            plan.commodity_futures_target_weight = 0.0
            plan.crisis_trend_target_weight = 0.0
            plan.dollar_squeeze_target_weight = 0.0
            plan.reversal_target_weight = 0.0
            plan.melt_up_target_weight = 0.0
            plan.reason = "POSITION_NAV_STOP"
            self._last_nav_stop_count += 1
        return out

    def _episode_label(self, as_of: date) -> str:
        ts = pd.Timestamp(as_of)
        if pd.Timestamp("2005-01-01") <= ts <= pd.Timestamp("2006-12-31"):
            return "pre_crisis_2005_2006"
        if pd.Timestamp("2007-01-01") <= ts <= pd.Timestamp("2009-12-31"):
            return "gfc_2007_2009"
        if pd.Timestamp("2010-01-01") <= ts <= pd.Timestamp("2013-12-31"):
            return "post_gfc_qe_2010_2013"
        if pd.Timestamp("2014-01-01") <= ts <= pd.Timestamp("2016-12-31"):
            return "commodity_growth_2014_2016"
        if pd.Timestamp("2017-01-01") <= ts <= pd.Timestamp("2019-12-31"):
            return "late_cycle_2017_2019"
        if pd.Timestamp("2020-01-01") <= ts <= pd.Timestamp("2020-12-31"):
            return "covid_2020"
        if pd.Timestamp("2021-01-01") <= ts <= pd.Timestamp("2022-12-31"):
            return "inflation_hiking_2021_2022"
        if pd.Timestamp("2023-01-01") <= ts <= pd.Timestamp("2024-12-31"):
            return "normalization_2023_2024"
        return "other"

    def _factor_exposures(self, *, symbol: str, asset_class: str) -> dict[str, float]:
        symbol = symbol.upper()
        asset_class = asset_class.upper()
        out = {
            "factor_equity": 0.0,
            "factor_duration": 0.0,
            "factor_credit": 0.0,
            "factor_commodity": 0.0,
            "factor_dollar": 0.0,
            "factor_gold": 0.0,
            "factor_inflation": 0.0,
        }
        if asset_class in {"EQUITY", "EQUITY_INDEX"}:
            out["factor_equity"] = 1.0
        elif asset_class in {"BOND", "DURATION", "RATES_FUTURE", "RATES_YIELD_FUTURE"} or symbol in {
            "TLT",
            "IEF",
            "ZT=F",
            "ZF=F",
            "ZN=F",
            "ZB=F",
            *RATE_YIELD_INSTRUMENTS,
        }:
            out["factor_duration"] = 1.0
        elif asset_class == "CREDIT" or symbol in {"HYG", "LQD", "EMB"}:
            out["factor_credit"] = 1.0
            out["factor_equity"] = 0.35
        elif asset_class in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY"} or symbol in {"DBC", "USO", "CL=F"}:
            out["factor_commodity"] = 1.0
        elif asset_class == "FX" or symbol in {"UUP", "DX=F"}:
            out["factor_dollar"] = 1.0
        elif asset_class == "GOLD" or symbol in {"GLD", "GC=F"}:
            out["factor_gold"] = 1.0
        elif asset_class in {"INFLATION_LINKED_BOND", "TIP"} or symbol == "TIP":
            out["factor_inflation"] = 1.0
            out["factor_duration"] = 0.5
        return out

    def _apply_engine_a_shutoff_reallocation(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        self._last_engine_a_reallocation_gross = 0.0
        self._last_engine_a_reallocation_count = 0
        if not self.enable_engine_a_reallocation_layer or not self._engine_a_shutoff_active or self.portfolio is None:
            return out

        baseline_carry_gross = sum(abs(self.carry_target_weights.get(symbol, 0.0)) for symbol in self.carry_symbols)
        if baseline_carry_gross <= 0.0:
            return out
        current_carry_target = sum(abs(out.get(symbol, BarbellTargetPlan(
            symbol=symbol,
            engine="ENGINE_A",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="CARRY",
            asset_class="CREDIT",
            reason="CARRY_BASE",
        )).target_weight) for symbol in self.carry_symbols)
        freed_gross = max(
            (baseline_carry_gross - current_carry_target) * self.engine_a_reallocation_deploy_fraction,
            0.0,
        )
        if freed_gross <= 0.0:
            return out

        eligible: list[tuple[str, float, float, int]] = []
        base_weight_total = 0.0
        for symbol in self.dislocation_symbols:
            plan = out.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            if plan.core_phase < self.engine_a_reallocation_phase_floor:
                continue
            if plan.core_target_weight == 0.0:
                continue
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity == 0 or np.sign(position.quantity) != np.sign(plan.core_target_weight):
                continue
            available_weight = max(self.engine_a_reallocation_max_symbol_weight - abs(plan.target_weight), 0.0)
            if available_weight <= 0.0:
                continue
            sign = 1 if plan.core_target_weight > 0 else -1
            base_weight = abs(plan.core_target_weight)
            eligible.append((symbol, base_weight, available_weight, sign))
            base_weight_total += base_weight

        if not eligible or base_weight_total <= 0.0:
            return out

        if self.enable_focused_reallocation_layer:
            focused = [item for item in eligible if out[item[0]].extreme_regime]
            if focused:
                best_symbol, _, available_weight, sign = max(
                    focused,
                    key=lambda item: (
                        abs(out[item[0]].score),
                        int(out[item[0]].core_phase),
                        abs(out[item[0]].core_target_weight),
                    ),
                )
                add_weight = min(freed_gross, available_weight)
                if add_weight > 0.0:
                    plan = out[best_symbol]
                    signed_addition = sign * add_weight
                    plan.reallocation_target_weight = signed_addition
                    plan.core_target_weight += signed_addition
                    self._refresh_composed_barbell_weight(plan)
                    self._last_engine_a_reallocation_gross = float(add_weight)
                    self._last_engine_a_reallocation_count = 1
                return out

        remaining = freed_gross
        allocations = {symbol: 0.0 for symbol, *_ in eligible}
        active = list(eligible)
        while remaining > 1e-9 and active:
            denominator = sum(base_weight for _, base_weight, _, _ in active)
            if denominator <= 0.0:
                break
            deployed = 0.0
            next_active: list[tuple[str, float, float, int]] = []
            for symbol, base_weight, available_weight, sign in active:
                proportional = remaining * (base_weight / denominator)
                remaining_room = max(available_weight - allocations[symbol], 0.0)
                add_weight = min(proportional, remaining_room)
                if add_weight > 0.0:
                    allocations[symbol] += add_weight
                    deployed += add_weight
                if allocations[symbol] + 1e-9 < available_weight:
                    next_active.append((symbol, base_weight, available_weight, sign))
            if deployed <= 1e-9:
                break
            remaining = max(remaining - deployed, 0.0)
            active = next_active

        for symbol, _, _, sign in eligible:
            add_weight = allocations.get(symbol, 0.0)
            if add_weight <= 0.0:
                continue
            plan = out[symbol]
            signed_addition = sign * add_weight
            plan.reallocation_target_weight = signed_addition
            plan.core_target_weight += signed_addition
            self._refresh_composed_barbell_weight(plan)

        self._last_engine_a_reallocation_gross = float(sum(allocations.values()))
        self._last_engine_a_reallocation_count = int(sum(1 for value in allocations.values() if value > 0.0))
        return out

    def _engine_b_extreme_coherence_threshold(self) -> float | None:
        history = np.fromiter(self._engine_b_coherence_history, dtype=float)
        if len(history) < self.engine_b_extreme_coherence_min_obs:
            return None
        return float(np.quantile(history, self.engine_b_extreme_coherence_quantile))

    def _engine_b_has_confirmed_position(self) -> bool:
        if self.portfolio is None:
            return False
        for symbol in self.dislocation_symbols:
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity == 0:
                continue
            state = self._engine_b_state.get(symbol)
            if state is None:
                continue
            if int(state.get("phase", 0)) >= self.engine_a_reallocation_phase_floor:
                return True
        return False

    def _engine_b_risk_direction(
        self,
        selected: list[tuple[str, float, str, str, str, float, float, float, float, float]],
    ) -> int:
        directional = 0.0
        for symbol, score, *_ in selected:
            sign = 1 if score > 0.0 else -1 if score < 0.0 else 0
            if sign == 0:
                continue
            # Long dollar is usually anti-risk / funding-stress; most other
            # Engine B symbols are risk/reflation expressions.
            direction = -sign if symbol in {"UUP", "DX=F"} else sign
            directional += abs(float(score)) * float(direction)
        if directional > 1e-9:
            return 1
        if directional < -1e-9:
            return -1
        return 0

    def _rates_macro_direction(self, region_states: dict[str, dict[str, float]]) -> tuple[int, float]:
        us = region_states.get("US", {})
        growth = float(us.get("growth", 0.0))
        inflation = float(us.get("inflation", 0.0))
        policy = float(us.get("policy", 0.0))
        credit = float(us.get("credit", 0.0))
        liquidity = float(us.get("liquidity", 0.0))
        recession_duration = max(-growth, -credit, -liquidity, 0.0) - 0.60 * max(inflation, policy, 0.0)
        inflation_duration_short = max(inflation, policy, 0.0) - 0.35 * max(-growth, -credit, 0.0)
        direction_score = recession_duration if recession_duration >= inflation_duration_short else -inflation_duration_short
        if abs(direction_score) < self.rates_curve_duration_short_entry_score:
            return 0, float(direction_score)
        # +1 is duration-long / anti-risk, -1 is duration-short / reflation.
        return (1 if direction_score > 0.0 else -1), float(direction_score)

    def _hg_satellite_direction(
        self,
        *,
        region_states: dict[str, dict[str, float]],
    ) -> int:
        if "HG=F" not in self.symbols:
            return 0
        us = region_states.get("US", {})
        growth = float(us.get("growth", 0.0))
        inflation = float(us.get("inflation", 0.0))
        policy = float(us.get("policy", 0.0))
        credit = float(us.get("credit", 0.0))
        liquidity = float(us.get("liquidity", 0.0))
        stress = max(-growth, -credit, -liquidity, 0.0)
        score = 0.75 * growth + 0.45 * inflation + 0.25 * liquidity - 0.35 * policy - 0.35 * stress
        momentum_63 = self._standardized_price_move("HG=F", move_lookback=63, vol_lookback=63)
        momentum_21 = self._standardized_price_move("HG=F", move_lookback=21, vol_lookback=42)
        momentum = 0.0
        if momentum_63 is not None:
            momentum += 0.65 * float(momentum_63)
        if momentum_21 is not None:
            momentum += 0.35 * float(momentum_21)
        if abs(score) < self.commodity_futures_price_only_entry_score:
            return 0
        if abs(momentum) < self.commodity_futures_price_only_min_momentum:
            return 0
        if np.sign(score) != np.sign(momentum):
            return 0
        return 1 if score > 0.0 else -1

    def _cross_sleeve_agreement_state(
        self,
        *,
        selected: list[tuple[str, float, str, str, str, float, float, float, float, float]],
        coherence_signal: float,
        carry_targets: dict[str, float],
        region_states: dict[str, dict[str, float]],
    ) -> tuple[bool, int, int]:
        if not self.enable_cross_sleeve_amplifier_layer or not selected:
            return False, 0, 0
        if coherence_signal < self.cross_sleeve_coherence_floor:
            return False, 0, 0
        if self.cross_sleeve_requires_confirmed_position and not self._engine_b_has_confirmed_position():
            return False, 0, 0
        risk_direction = self._engine_b_risk_direction(selected)
        if risk_direction == 0:
            return False, 0, 0

        alignment_count = 0
        if max(abs(score) for _, score, *_ in selected) >= self.engine_b_conviction_threshold:
            alignment_count += 1

        base_carry_gross = sum(abs(self.carry_target_weights.get(symbol, 0.0)) for symbol in self.carry_symbols)
        carry_gross = sum(abs(weight) for weight in carry_targets.values())
        carry_scale = carry_gross / max(base_carry_gross, 1e-9)
        if risk_direction > 0 and carry_scale >= 0.85:
            alignment_count += 1
        elif risk_direction < 0 and (
            self._engine_a_shutoff_active or bool(self._last_engine_b_feedback.get("a_stress", False))
        ):
            alignment_count += 1

        rates_direction, rates_score = self._rates_macro_direction(region_states)
        if rates_direction != 0 and abs(rates_score) >= self.rates_curve_duration_short_entry_score:
            if (risk_direction < 0 and rates_direction > 0) or (risk_direction > 0 and rates_direction < 0):
                alignment_count += 1

        if not self._reversal_state:
            alignment_count += 1

        hg_direction = self._hg_satellite_direction(region_states=region_states)
        if hg_direction != 0 and hg_direction == risk_direction:
            alignment_count += 1

        return (
            alignment_count >= self.cross_sleeve_min_alignment_count,
            int(alignment_count),
            int(risk_direction),
        )

    def _apply_transmission_overlay(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        market_event: MarketEvent,
        prices: dict[str, float],
        equity: float,
    ) -> dict[str, BarbellTargetPlan]:
        out = self._clone_barbell_plans(plans)
        if self.overlay_off:
            self._overlay_last_snapshot = {
                symbol: {
                    "leader_score": 0.0,
                    "lag_score": 0.0,
                    "threshold": 0.0,
                    "overlay_weight": 0.0,
                    "active": False,
                    "reason": "OVERLAY_OFF",
                }
                for symbol in self.overlay_symbols
            }
            return out
        engine_a_leader = self._engine_a_pnl_5d_zscore()
        uup_move = self._standardized_price_move("UUP") if "UUP" in self.symbols else None
        overlay_snapshots: dict[str, dict[str, float | str | bool]] = {}
        leader_updates: list[tuple[str, float]] = []
        routing_requests: list[tuple[str, int, float, float]] = []

        for symbol in self.overlay_symbols:
            plan = out.get(symbol)
            if plan is None:
                continue
            core_weight = float(plan.target_weight)
            plan.core_target_weight = core_weight
            plan.overlay_target_weight = 0.0
            plan.overlay_reason = ""
            plan.leader_score = 0.0
            plan.lag_score = 0.0
            plan.overlay_threshold = self._overlay_leader_threshold(symbol)
            plan.reallocation_target_weight = 0.0

            snapshot = {
                "leader_score": 0.0,
                "lag_score": 0.0,
                "threshold": plan.overlay_threshold,
                "overlay_weight": 0.0,
                "active": False,
                "reason": "NO_CORE",
            }
            if plan.engine != "ENGINE_B" or core_weight == 0.0:
                overlay_snapshots[symbol] = snapshot
                continue

            sign = 1 if core_weight > 0 else -1
            slow_move = self._standardized_price_move(symbol)
            weighted_sum = 0.0
            weight_total = 0.0
            if engine_a_leader is not None:
                weighted_sum += 0.60 * sign * engine_a_leader
                weight_total += 0.60
            if uup_move is not None:
                weighted_sum += 0.40 * sign * (-uup_move)
                weight_total += 0.40
            if weight_total <= 0.0 or slow_move is None:
                overlay_snapshots[symbol] = snapshot
                continue

            leader_score = float(weighted_sum / weight_total)
            leader_updates.append((symbol, max(leader_score, 0.0)))
            lag_score = (
                float(leader_score * (1.0 - ((sign * slow_move) / (leader_score + 1e-9))))
                if leader_score > 0.0
                else 0.0
            )
            entry_floor = max(self.overlay_exit_lag, 0.5 * plan.overlay_threshold)
            prior_state = self._overlay_state.get(symbol)
            active = False
            reason = "ENGINE_B_OVERLAY_IDLE"

            if prior_state is not None:
                if int(prior_state["sign"]) != sign:
                    reason = "ENGINE_B_OVERLAY_EXIT"
                elif self._velocity_decelerated(
                    current_velocity=plan.velocity_signal,
                    peak_velocity=float(prior_state.get("peak_velocity", 0.0)),
                ):
                    reason = "ENGINE_B_OVERLAY_VELOCITY_EXIT"
                elif lag_score < self.overlay_exit_lag or leader_score < plan.overlay_threshold * 0.85:
                    reason = "ENGINE_B_OVERLAY_EXIT"
                else:
                    active = True
                    reason = "ENGINE_B_OVERLAY_HOLD"
            elif leader_score >= plan.overlay_threshold and lag_score >= entry_floor:
                active = True
                reason = "ENGINE_B_OVERLAY_ENTRY"

            overlay_weight = 0.0
            if active:
                intensity = min(max((lag_score - entry_floor) / max(plan.overlay_threshold, 1e-9), 0.0), 1.0)
                max_overlay = abs(core_weight) * self.overlay_max_core_fraction
                available = max(self.overlay_max_asset_weight - abs(core_weight), 0.0)
                requested_abs = min(max_overlay * intensity, available)
                if self.enable_overlay_routing_layer:
                    requested_abs = min(
                        available,
                        max(self.overlay_routing_min_weight, requested_abs),
                    )
                    routing_requests.append((symbol, sign, requested_abs, lag_score))
                    plan.target_weight = core_weight
                    plan.overlay_target_weight = 0.0
                    plan.overlay_reason = reason
                else:
                    overlay_weight = sign * requested_abs
                    plan.target_weight = core_weight + overlay_weight + plan.convex_target_weight
                    plan.overlay_target_weight = overlay_weight
                    plan.overlay_reason = reason
            else:
                plan.target_weight = core_weight + plan.convex_target_weight
                plan.overlay_target_weight = 0.0
                plan.overlay_reason = reason if prior_state is not None else ""

            plan.leader_score = leader_score
            plan.lag_score = lag_score
            snapshot.update(
                {
                    "leader_score": leader_score,
                    "lag_score": lag_score,
                    "threshold": plan.overlay_threshold,
                    "overlay_weight": overlay_weight,
                    "active": bool(active and overlay_weight != 0.0),
                    "reason": reason,
                }
            )
            overlay_snapshots[symbol] = snapshot

        if self.enable_overlay_routing_layer and routing_requests:
            for symbol, sign, requested_abs, lag_score in sorted(routing_requests, key=lambda item: item[3], reverse=True):
                plan = out[symbol]
                core_weight = abs(float(plan.core_target_weight))
                room = max(self.overlay_max_asset_weight - core_weight, 0.0)
                target_abs = min(requested_abs, room)
                if target_abs <= 0.0:
                    plan.overlay_reason = "ENGINE_B_OVERLAY_NO_ROUTE"
                    snapshot = overlay_snapshots.get(symbol)
                    if snapshot is not None:
                        snapshot["reason"] = "ENGINE_B_OVERLAY_NO_ROUTE"
                    continue

                donors: list[tuple[int, str, int, int, float]] = []
                for donor_symbol in self.dislocation_symbols:
                    if donor_symbol == symbol:
                        continue
                    donor_plan = out.get(donor_symbol)
                    if donor_plan is None or donor_plan.engine != "ENGINE_B":
                        continue
                    donor_sign = 1 if donor_plan.core_target_weight > 0 else -1 if donor_plan.core_target_weight < 0 else 0
                    donor_phase = int(donor_plan.core_phase)
                    if donor_phase <= 0 or donor_phase >= 3:
                        continue
                    donor_core_abs = abs(float(donor_plan.core_target_weight))
                    donor_available = min(
                        donor_core_abs * self.overlay_routing_max_donor_fraction,
                        max(donor_core_abs - self.overlay_routing_min_residual_weight, 0.0),
                    )
                    if donor_available <= 0.0:
                        continue
                    priority = 0 if donor_sign == sign else 1
                    donors.append((priority, donor_symbol, donor_sign, donor_phase, donor_available))

                donors.sort(key=lambda item: (item[0], item[3], abs(out[item[1]].score)))
                routed_abs = 0.0
                for _, donor_symbol, donor_sign, _, donor_available in donors:
                    remaining = target_abs - routed_abs
                    if remaining <= 1e-9:
                        break
                    take = min(remaining, donor_available)
                    if take <= 0.0:
                        continue
                    donor_plan = out[donor_symbol]
                    donor_plan.core_target_weight -= donor_sign * take
                    self._refresh_composed_barbell_weight(donor_plan)
                    donor_plan.reason = "ENGINE_B_OVERLAY_ROUTE_OUT"
                    routed_abs += take

                if routed_abs > 0.0:
                    overlay_weight = sign * routed_abs
                    plan.overlay_target_weight = overlay_weight
                    self._refresh_composed_barbell_weight(plan)
                    snapshot = overlay_snapshots.get(symbol)
                    if snapshot is not None:
                        snapshot["overlay_weight"] = overlay_weight
                        snapshot["active"] = True
                else:
                    plan.target_weight = plan.core_target_weight + plan.convex_target_weight
                    plan.overlay_target_weight = 0.0
                    plan.overlay_reason = "ENGINE_B_OVERLAY_NO_ROUTE"
                    snapshot = overlay_snapshots.get(symbol)
                    if snapshot is not None:
                        snapshot["overlay_weight"] = 0.0
                        snapshot["active"] = False
                        snapshot["reason"] = "ENGINE_B_OVERLAY_NO_ROUTE"

        self._overlay_last_snapshot = overlay_snapshots
        for symbol, value in leader_updates:
            if value > 0.0:
                self._overlay_leader_history[symbol].append(value)
        return out

    def _sync_overlay_state(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        projected_positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
    ) -> None:
        next_state: dict[str, dict[str, float | int]] = {}
        for symbol in self.overlay_symbols:
            plan = plans.get(symbol)
            price = prices.get(symbol)
            if plan is None or price is None:
                continue
            if plan.overlay_target_weight == 0.0:
                continue
            total_qty = int(projected_positions.get(symbol, 0))
            core_qty = _weight_target_size(
                equity=equity,
                price=price * self._instrument_multiplier(symbol),
                target_weight=plan.core_target_weight,
            )
            if total_qty == 0 or np.sign(total_qty) != np.sign(plan.overlay_target_weight):
                continue
            if abs(total_qty) <= abs(core_qty) + 1:
                continue
            prior = self._overlay_state.get(symbol)
            entry_bar = (
                int(prior["entry_bar"])
                if prior is not None and int(prior["sign"]) == (1 if plan.overlay_target_weight > 0 else -1)
                else self._bar_index
            )
            entry_price = (
                float(prior.get("entry_price", price))
                if prior is not None and int(prior["sign"]) == (1 if plan.overlay_target_weight > 0 else -1)
                else float(price)
            )
            peak_price = float(price)
            if prior is not None and int(prior["sign"]) == (1 if plan.overlay_target_weight > 0 else -1):
                previous_peak = float(prior.get("peak_price", price))
                peak_price = max(previous_peak, float(price)) if plan.overlay_target_weight > 0 else min(previous_peak, float(price))
            peak_lag = max(
                plan.lag_score,
                float(prior.get("peak_lag", plan.lag_score)) if prior is not None else plan.lag_score,
            )
            next_state[symbol] = {
                "entry_bar": entry_bar,
                "sign": 1 if plan.overlay_target_weight > 0 else -1,
                "entry_price": entry_price,
                "peak_price": peak_price,
                "peak_lag": peak_lag,
                "peak_velocity": max(
                    plan.velocity_signal,
                    float(prior.get("peak_velocity", 0.0)) if prior is not None else 0.0,
                ),
                "phase": plan.overlay_phase,
            }
        self._overlay_state = next_state

    def _sync_engine_b_state(
        self,
        *,
        plans: dict[str, BarbellTargetPlan],
        projected_positions: dict[str, int],
        prices: dict[str, float],
    ) -> None:
        next_state: dict[str, dict[str, float | int]] = {}
        for symbol in self.dislocation_symbols:
            plan = plans.get(symbol)
            if plan is None or plan.engine != "ENGINE_B":
                continue
            qty = int(projected_positions.get(symbol, 0))
            price = prices.get(symbol)
            if price is None:
                continue
            if qty == 0:
                continue
            sign = 1 if qty > 0 else -1
            prior = self._engine_b_state.get(symbol)
            peak_score = abs(plan.score)
            peak_velocity = plan.velocity_signal
            entry_bar = self._bar_index
            entry_price = float(price)
            if prior is not None and int(prior["sign"]) == sign:
                peak_score = max(peak_score, float(prior["peak_score"]))
                peak_velocity = max(peak_velocity, float(prior["peak_velocity"]))
                entry_bar = int(prior.get("entry_bar", self._bar_index))
                entry_price = float(prior.get("entry_price", price))
            next_state[symbol] = {
                "sign": sign,
                "peak_score": peak_score,
                "peak_velocity": peak_velocity,
                "entry_bar": entry_bar,
                "entry_price": entry_price,
                "phase": plan.core_phase,
            }
        self._engine_b_state = next_state

    def _build_barbell_plans(
        self,
        *,
        as_of: date,
        sector_map: dict[str, str],
        region_map: dict[str, str],
        asset_map: dict[str, str],
        region_states: dict[str, dict[str, float]],
        region_snapshots: dict[str, object],
        region_themes: dict[str, str],
        region_velocity_signals: dict[str, float],
        market_event: MarketEvent,
    ) -> dict[str, BarbellTargetPlan]:
        plans: dict[str, BarbellTargetPlan] = {}
        equity = max(float(self.portfolio.latest_equity), 1e-9) if self.portfolio is not None else 1.0
        feedback = self._engine_b_feedback_state(as_of=as_of, equity=equity)
        long_threshold_mult = float(feedback["long_threshold_multiplier"])
        short_threshold_mult = float(feedback["short_threshold_multiplier"])
        self._last_cross_sleeve_amplifier_active = False
        self._last_cross_sleeve_alignment_count = 0
        self._last_cross_sleeve_direction = 0
        self._last_cross_sleeve_gross_multiplier = 1.0
        carry_reason = "CARRY_BASE"
        if self._engine_a_shutoff_active:
            carry_reason = self._engine_a_last_trigger_reason
        carry_targets = self._engine_a_target_weights(region_states=region_states, region_themes=region_themes)
        if self._engine_a_reentry_step is not None and sum(abs(v) for v in carry_targets.values()) < sum(
            abs(self.carry_target_weights.get(symbol, 0.0)) for symbol in self.carry_symbols
        ):
            carry_reason = "CARRY_REENTRY"
        for symbol in self.carry_symbols:
            plans[symbol] = BarbellTargetPlan(
                symbol=symbol,
                engine="ENGINE_A",
                target_weight=carry_targets.get(symbol, 0.0),
                score=carry_targets.get(symbol, 0.0),
                region=region_map.get(symbol, "US"),
                theme="CARRY",
                asset_class=asset_map.get(symbol, "CREDIT"),
                reason=carry_reason,
                core_target_weight=carry_targets.get(symbol, 0.0),
            )

        relative_overlays = self._relative_overlays(region_states, region_themes)
        dislocation_scores: list[tuple[str, float, str, str, str, float, float, float, float, float]] = []
        for symbol in self.dislocation_symbols if self.enable_engine_b_core else ():
            if symbol not in market_event.bars:
                continue
            score, region, theme, reason, coherence, velocity_signal = self._engine_b_score(
                symbol=symbol,
                as_of=as_of,
                sector_map=sector_map,
                region_map=region_map,
                asset_map=asset_map,
                region_states=region_states,
                region_snapshots=region_snapshots,
                region_themes=region_themes,
                region_velocity_signals=region_velocity_signals,
                relative_overlays=relative_overlays,
            )
            threshold_mult = self._sensor_threshold_multiplier(
                symbol=symbol,
                score=score,
                base_multiplier=long_threshold_mult if score >= 0.0 else short_threshold_mult,
                feedback=feedback,
            )
            probe_threshold = self.engine_b_probe_threshold * threshold_mult
            conviction_threshold = self.engine_b_conviction_threshold * threshold_mult
            max_threshold = self.engine_b_max_threshold * threshold_mult
            probe_threshold, conviction_threshold, max_threshold = self._adaptive_engine_b_thresholds(
                coherence=coherence,
                velocity_signal=velocity_signal,
                probe_threshold=probe_threshold,
                conviction_threshold=conviction_threshold,
                max_threshold=max_threshold,
            )
            dislocation_scores.append(
                (
                    symbol,
                    score,
                    region,
                    theme,
                    reason,
                    probe_threshold,
                    conviction_threshold,
                    max_threshold,
                    coherence,
                    velocity_signal,
                )
            )

        ranked = [row for row in dislocation_scores if abs(row[1]) >= row[5]]
        ranked.sort(key=lambda item: abs(item[1]), reverse=True)
        gross_target = 0.0
        max_score = 0.0
        coherence_signal = 0.0
        for _, score, _, _, _, probe_threshold, conviction_threshold, max_threshold, _, _ in ranked:
            gross_target = max(
                gross_target,
                self._engine_b_target_gross(
                    abs(score),
                    probe_threshold=probe_threshold,
                    conviction_threshold=conviction_threshold,
                    max_threshold=max_threshold,
                ),
            )
            max_score = max(max_score, abs(score))

        if ranked:
            coherence_signal = float(
                sum(abs(score) * coherence for _, score, _, _, _, _, _, _, coherence, _ in ranked)
                / max(sum(abs(score) for _, score, *_ in ranked), 1e-9)
            )
        coherence_threshold = self._engine_b_extreme_coherence_threshold()
        extreme_active = bool(
            self.enable_extreme_concentration_layer
            and ranked
            and coherence_threshold is not None
            and coherence_signal >= coherence_threshold
            and self._engine_b_has_confirmed_position()
        )
        selected_top_n = self.engine_b_extreme_top_n if extreme_active else self.engine_b_top_n

        selected = self._correlation_aware_select(ranked, top_n=selected_top_n)
        cross_sleeve_active, cross_sleeve_count, cross_sleeve_direction = self._cross_sleeve_agreement_state(
            selected=selected,
            coherence_signal=coherence_signal,
            carry_targets=carry_targets,
            region_states=region_states,
        )
        gross_cap = self.engine_b_max_gross
        gross_multiplier = 1.0
        if cross_sleeve_active:
            gross_cap = max(self.engine_b_max_gross, self.cross_sleeve_engine_b_max_gross)
            gross_multiplier = self.cross_sleeve_gross_multiplier
            self._last_cross_sleeve_amplifier_active = True
            self._last_cross_sleeve_alignment_count = int(cross_sleeve_count)
            self._last_cross_sleeve_direction = int(cross_sleeve_direction)
            self._last_cross_sleeve_gross_multiplier = float(gross_multiplier)
        velocity_multiplier_by_symbol = {
            symbol: self._engine_b_velocity_multiplier(coherence=coherence, velocity_signal=velocity_signal)
            for symbol, _, _, _, _, _, _, _, coherence, velocity_signal in selected
        }
        if selected:
            velocity_weighted_multiplier = float(
                sum(abs(score) * velocity_multiplier_by_symbol.get(symbol, 1.0) for symbol, score, *_ in selected)
                / max(sum(abs(score) for symbol, score, *_ in selected), 1e-9)
            )
            gross_target = min(gross_cap, gross_target * velocity_weighted_multiplier * gross_multiplier)
            self._last_engine_b_velocity_multiplier = velocity_weighted_multiplier
            self._last_engine_b_velocity_signal = float(
                max(velocity_signal for _, _, _, _, _, _, _, _, _, velocity_signal in selected)
            )
        else:
            self._last_engine_b_velocity_multiplier = 1.0
            self._last_engine_b_velocity_signal = 0.0
        self._last_engine_b_scale = gross_target
        self._last_engine_b_max_score = max_score
        self._last_engine_b_extreme_active = extreme_active
        self._last_engine_b_extreme_coherence = coherence_signal
        self._last_engine_b_extreme_threshold = float(coherence_threshold or 0.0)
        if ranked:
            self._engine_b_coherence_history.append(coherence_signal)

        score_denominator = max(sum(abs(score) for _, score, _, _, _, _, _, _, _, _ in selected), 1e-9)
        selected_symbols = {symbol for symbol, _, _, _, _, _, _, _, _, _ in selected}
        for symbol, score, region, theme, reason, _, _, _, coherence, velocity_signal in dislocation_scores:
            target_weight = 0.0
            velocity_multiplier = velocity_multiplier_by_symbol.get(symbol, 1.0)
            template = self._exposure_template(
                asset_class=asset_map.get(symbol, "EQUITY"),
                sector=sector_map.get(symbol, "UNSPECIFIED"),
            )
            macro_surprise_score = self._macro_surprise_weighted_score(
                region=region,
                as_of=as_of,
                weights=template,
            )
            macro_surprise_state = self._macro_surprise_state(region, as_of)
            macro_surprise_confirmed = bool(
                self.enable_macro_surprise_layer
                and abs(macro_surprise_score) >= self.macro_surprise_confirm_threshold
                and score * macro_surprise_score > 0.0
            )
            if symbol in selected_symbols and gross_target > 0:
                raw_weight = gross_target * abs(score) / score_denominator
                bar = market_event.bars.get(symbol)
                closes = np.fromiter(self._closes[symbol], dtype=float)
                sign = 1 if score > 0 else -1
                trend_period = self._trend_period_for_symbol(
                    symbol=symbol,
                    asset_class=asset_map.get(symbol, "EQUITY"),
                    sign=sign,
                )
                trend = _sma(closes, trend_period)
                trend_multiplier = 1.0
                if bar is not None:
                    highs = np.fromiter(self._highs[symbol], dtype=float)
                    lows = np.fromiter(self._lows[symbol], dtype=float)
                    atr_value = _atr(highs, lows, closes, self.atr_period)
                    trend_multiplier = self._trend_multiplier(
                        sign=sign,
                        close=bar.close,
                        trend=trend,
                        atr_value=atr_value,
                        asset_class=asset_map.get(symbol, "EQUITY"),
                    )
                target_weight = float(np.sign(score) * raw_weight * trend_multiplier)
            plans[symbol] = BarbellTargetPlan(
                symbol=symbol,
                engine="ENGINE_B",
                target_weight=target_weight,
                score=score,
                region=region,
                theme=theme,
                asset_class=asset_map.get(symbol, "EQUITY"),
                reason=reason if target_weight != 0 else "DISLOCATION_DORMANT",
                core_target_weight=target_weight,
                coherence=coherence,
                velocity_signal=velocity_signal,
                velocity_multiplier=velocity_multiplier,
                macro_surprise_score=macro_surprise_score,
                macro_surprise_coherence=float(macro_surprise_state["coherence"]),
                macro_surprise_confirmed=macro_surprise_confirmed,
                reallocation_target_weight=0.0,
                extreme_regime=bool(extreme_active and symbol in selected_symbols),
                cross_sleeve_amplifier=bool(cross_sleeve_active and symbol in selected_symbols),
                cross_sleeve_alignment_count=int(cross_sleeve_count if symbol in selected_symbols else 0),
            )

        for symbol in self.symbols:
            if symbol not in plans:
                plans[symbol] = BarbellTargetPlan(
                    symbol=symbol,
                    engine="UNASSIGNED",
                    target_weight=0.0,
                    score=0.0,
                    region=region_map.get(symbol, "GLOBAL"),
                    theme="NONE",
                    asset_class=asset_map.get(symbol, "EQUITY"),
                    reason="UNUSED",
                    core_target_weight=0.0,
                )
        return plans

    def _barbell_target_quantity(
        self,
        *,
        plan: BarbellTargetPlan,
        price: float,
        current_qty: int,
    ) -> int:
        if self.portfolio is None:
            return 0
        if plan.target_weight == 0.0:
            return 0
        equity = max(float(self.portfolio.latest_equity), 1e-9)
        desired_qty = _weight_target_size(
            equity=equity,
            price=price * self._instrument_multiplier(plan.symbol),
            target_weight=plan.target_weight,
        )
        if current_qty == 0 or desired_qty == 0 or np.sign(current_qty) != np.sign(desired_qty):
            return desired_qty
        delta = abs(desired_qty - current_qty)
        if delta < 3:
            return current_qty
        if delta / max(abs(current_qty), 1) < 0.05:
            return current_qty
        return desired_qty

    def _emit_barbell_delta(
        self,
        *,
        market_event: MarketEvent,
        plan: BarbellTargetPlan,
        current_qty: int,
        desired_qty: int,
    ) -> SignalEvent | None:
        delta = desired_qty - current_qty
        if delta == 0:
            return None
        reason = plan.reason
        explicit_engine_b_exit_reasons = {"TRAILING_STOP", "ENGINE_B_VELOCITY_EXIT", "ENGINE_B_PHASE_STOP"}
        if plan.engine == "ENGINE_B":
            if plan.overlay_reason and current_qty != 0 and desired_qty != 0:
                reason = plan.overlay_reason
            elif current_qty == 0 and desired_qty != 0:
                reason = "ENGINE_B_ENTRY"
            elif current_qty != 0 and desired_qty == 0:
                if plan.reason not in explicit_engine_b_exit_reasons:
                    reason = "ENGINE_B_EXIT"
            elif current_qty * desired_qty < 0:
                reason = "ENGINE_B_FLIP"
        metadata = {
            "strategy": "GLOBAL_MACRO_BARBELL",
            "engine": plan.engine,
            "reason": reason,
            "score": plan.score,
            "region": plan.region,
            "theme": plan.theme,
            "asset_class": plan.asset_class,
            "target_qty": desired_qty,
            "target_weight": plan.target_weight,
            "core_target_weight": plan.core_target_weight,
            "overlay_target_weight": plan.overlay_target_weight,
            "convex_target_weight": plan.convex_target_weight,
            "hedge_target_weight": plan.hedge_target_weight,
            "trend_sleeve_target_weight": plan.trend_sleeve_target_weight,
            "rates_curve_target_weight": plan.rates_curve_target_weight,
            "curve_rv_target_weight": plan.curve_rv_target_weight,
            "commodity_futures_target_weight": plan.commodity_futures_target_weight,
            "crisis_trend_target_weight": plan.crisis_trend_target_weight,
            "dollar_squeeze_target_weight": plan.dollar_squeeze_target_weight,
            "reversal_target_weight": plan.reversal_target_weight,
            "melt_up_target_weight": plan.melt_up_target_weight,
            "leader_score": plan.leader_score,
            "lag_score": plan.lag_score,
            "coherence": plan.coherence,
            "velocity_signal": plan.velocity_signal,
            "velocity_multiplier": plan.velocity_multiplier,
            "macro_surprise_score": plan.macro_surprise_score,
            "macro_surprise_coherence": plan.macro_surprise_coherence,
            "macro_surprise_confirmed": plan.macro_surprise_confirmed,
            "core_phase": plan.core_phase,
            "overlay_phase": plan.overlay_phase,
            "core_phase_multiplier": plan.core_phase_multiplier,
            "overlay_phase_multiplier": plan.overlay_phase_multiplier,
            "reallocation_target_weight": plan.reallocation_target_weight,
            "extreme_regime": plan.extreme_regime,
            "cross_sleeve_amplifier": plan.cross_sleeve_amplifier,
            "cross_sleeve_alignment_count": plan.cross_sleeve_alignment_count,
            "convex_reason": plan.convex_reason,
            "hedge_reason": plan.hedge_reason,
            "trend_sleeve_reason": plan.trend_sleeve_reason,
            "rates_curve_reason": plan.rates_curve_reason,
            "curve_rv_reason": plan.curve_rv_reason,
            "commodity_futures_reason": plan.commodity_futures_reason,
            "crisis_trend_reason": plan.crisis_trend_reason,
            "dollar_squeeze_reason": plan.dollar_squeeze_reason,
            "reversal_reason": plan.reversal_reason,
            "melt_up_reason": plan.melt_up_reason,
        }
        if delta > 0:
            return self.buy_moo(
                timestamp=market_event.timestamp,
                symbol=plan.symbol,
                quantity=int(delta),
                metadata=metadata,
            )
        metadata["short_sale"] = desired_qty < 0
        return self.sell_moo(
            timestamp=market_event.timestamp,
            symbol=plan.symbol,
            quantity=int(abs(delta)),
            metadata=metadata,
        )

    def _sync_barbell_trade_context(
        self,
        *,
        market_event: MarketEvent,
        plans: dict[str, BarbellTargetPlan],
        projected_positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
    ) -> None:
        if self.portfolio is None or equity <= 0.0:
            return
        for symbol in self.symbols:
            position = self.portfolio.position_for_symbol(symbol)
            if int(position.quantity) == 0:
                continue
            plan = plans.get(symbol)
            price = prices.get(symbol)
            if plan is None or price is None:
                continue
            projected_qty = int(projected_positions.get(symbol, int(position.quantity)))
            effective_target_weight = float(self._instrument_notional(symbol, projected_qty, price) / equity)
            current_weight = float(self._instrument_notional(symbol, position.quantity, price) / equity)
            metadata = {
                "strategy": "GLOBAL_MACRO_BARBELL",
                "engine": plan.engine,
                "reason": plan.reason,
                "score": plan.score,
                "region": plan.region,
                "theme": plan.theme,
                "asset_class": plan.asset_class,
                "target_qty": projected_qty,
                "target_weight": effective_target_weight,
                "model_target_weight": plan.target_weight,
                "current_qty": int(position.quantity),
                "current_weight": current_weight,
                "core_target_weight": plan.core_target_weight,
                "overlay_target_weight": plan.overlay_target_weight,
                "convex_target_weight": plan.convex_target_weight,
                "hedge_target_weight": plan.hedge_target_weight,
                "trend_sleeve_target_weight": plan.trend_sleeve_target_weight,
                "rates_curve_target_weight": plan.rates_curve_target_weight,
                "curve_rv_target_weight": plan.curve_rv_target_weight,
                "commodity_futures_target_weight": plan.commodity_futures_target_weight,
                "crisis_trend_target_weight": plan.crisis_trend_target_weight,
                "dollar_squeeze_target_weight": plan.dollar_squeeze_target_weight,
                "reversal_target_weight": plan.reversal_target_weight,
                "melt_up_target_weight": plan.melt_up_target_weight,
                "leader_score": plan.leader_score,
                "lag_score": plan.lag_score,
                "coherence": plan.coherence,
                "velocity_signal": plan.velocity_signal,
                "velocity_multiplier": plan.velocity_multiplier,
                "macro_surprise_score": plan.macro_surprise_score,
                "macro_surprise_coherence": plan.macro_surprise_coherence,
                "macro_surprise_confirmed": plan.macro_surprise_confirmed,
                "core_phase": plan.core_phase,
                "overlay_phase": plan.overlay_phase,
                "core_phase_multiplier": plan.core_phase_multiplier,
                "overlay_phase_multiplier": plan.overlay_phase_multiplier,
                "reallocation_target_weight": plan.reallocation_target_weight,
                "extreme_regime": plan.extreme_regime,
                "cross_sleeve_amplifier": plan.cross_sleeve_amplifier,
                "cross_sleeve_alignment_count": plan.cross_sleeve_alignment_count,
                "convex_reason": plan.convex_reason,
                "hedge_reason": plan.hedge_reason,
                "trend_sleeve_reason": plan.trend_sleeve_reason,
                "rates_curve_reason": plan.rates_curve_reason,
                "curve_rv_reason": plan.curve_rv_reason,
                "commodity_futures_reason": plan.commodity_futures_reason,
                "crisis_trend_reason": plan.crisis_trend_reason,
                "dollar_squeeze_reason": plan.dollar_squeeze_reason,
                "reversal_reason": plan.reversal_reason,
                "melt_up_reason": plan.melt_up_reason,
            }
            self.portfolio.update_trade_context(
                symbol=symbol,
                metadata=metadata,
                timestamp=market_event.timestamp,
            )

    def _record_barbell_daily(
        self,
        *,
        as_of: date,
        bars: dict[str, object],
        equity: float,
    ) -> None:
        current_engine_a_pnl = self._sleeve_total_pnl(self.carry_symbols, bars)
        current_engine_b_pnl = self._sleeve_total_pnl(self.dislocation_symbols, bars)
        active_engine_b_plans = [
            plan
            for plan in self._last_barbell_plans.values()
            if plan.engine == "ENGINE_B" and abs(plan.core_target_weight) > 0.0
        ]
        overlay_rows = list(self._overlay_last_snapshot.values())
        overlay_active = [row for row in overlay_rows if bool(row.get("active"))]
        overlay_leader_mean = float(np.mean([float(row["leader_score"]) for row in overlay_rows])) if overlay_rows else 0.0
        overlay_lag_mean = float(np.mean([float(row["lag_score"]) for row in overlay_rows])) if overlay_rows else 0.0
        overlay_threshold_mean = (
            float(np.mean([float(row["threshold"]) for row in overlay_rows]))
            if overlay_rows
            else 0.0
        )
        overlay_weight_mean = (
            float(np.mean([abs(float(row["overlay_weight"])) for row in overlay_active]))
            if overlay_active
            else 0.0
        )
        engine_b_velocity_signal_mean = (
            float(np.mean([plan.velocity_signal for plan in active_engine_b_plans]))
            if active_engine_b_plans
            else 0.0
        )
        engine_b_velocity_multiplier_mean = (
            float(np.mean([plan.velocity_multiplier for plan in active_engine_b_plans]))
            if active_engine_b_plans
            else 1.0
        )
        engine_b_phase_mean = (
            float(np.mean([plan.core_phase for plan in active_engine_b_plans]))
            if active_engine_b_plans
            else 0.0
        )
        engine_b_phase3_count = float(sum(1 for plan in active_engine_b_plans if plan.core_phase >= 3))
        macro_surprise_confirm_count = float(
            sum(1 for plan in active_engine_b_plans if plan.macro_surprise_confirmed)
        )
        macro_surprise_score_mean = (
            float(np.mean([abs(plan.macro_surprise_score) for plan in active_engine_b_plans]))
            if active_engine_b_plans
            else 0.0
        )
        engine_b_convex_active = [plan for plan in active_engine_b_plans if plan.convex_target_weight != 0.0]
        hedge_active = [plan for plan in self._last_barbell_plans.values() if plan.hedge_target_weight != 0.0]
        trend_sleeve_active = [
            plan for plan in self._last_barbell_plans.values() if plan.trend_sleeve_target_weight != 0.0
        ]
        rates_curve_active = [
            plan for plan in self._last_barbell_plans.values() if plan.rates_curve_target_weight != 0.0
        ]
        curve_rv_active = [
            plan for plan in self._last_barbell_plans.values() if plan.curve_rv_target_weight != 0.0
        ]
        commodity_futures_active = [
            plan for plan in self._last_barbell_plans.values() if plan.commodity_futures_target_weight != 0.0
        ]
        engine_b_convex_weight_mean = (
            float(np.mean([abs(plan.convex_target_weight) for plan in engine_b_convex_active]))
            if engine_b_convex_active
            else 0.0
        )
        crisis_trend_active = [plan for plan in active_engine_b_plans if plan.crisis_trend_target_weight != 0.0]
        dollar_squeeze_active = [plan for plan in active_engine_b_plans if plan.dollar_squeeze_target_weight != 0.0]
        reversal_active = [plan for plan in active_engine_b_plans if plan.reversal_target_weight != 0.0]
        melt_up_active = [plan for plan in active_engine_b_plans if plan.melt_up_target_weight != 0.0]
        overlay_phase_mean = (
            float(np.mean([plan.overlay_phase for plan in active_engine_b_plans if plan.overlay_target_weight != 0.0]))
            if any(plan.overlay_target_weight != 0.0 for plan in active_engine_b_plans)
            else 0.0
        )
        engine_b_phase_stop_count = float(
            sum(1 for plan in self._last_barbell_plans.values() if plan.reason == "ENGINE_B_PHASE_STOP")
        )
        overlay_phase_stop_count = float(
            sum(
                1
                for plan in self._last_barbell_plans.values()
                if plan.overlay_reason in {"ENGINE_B_OVERLAY_PHASE_STOP", "ENGINE_B_OVERLAY_PHASE_EXIT"}
            )
        )
        engine_b_velocity_exit_count = float(
            sum(1 for plan in self._last_barbell_plans.values() if plan.reason == "ENGINE_B_VELOCITY_EXIT")
        )
        overlay_velocity_exit_count = float(
            sum(1 for row in overlay_rows if row.get("reason") == "ENGINE_B_OVERLAY_VELOCITY_EXIT")
        )
        self._barbell_daily_rows.append(
            {
                "date": as_of.isoformat(),
                "engine_a_gross": self._sleeve_gross_exposure_ratio(self.carry_symbols, bars, equity),
                "engine_b_gross": self._sleeve_gross_exposure_ratio(self.dislocation_symbols, bars, equity),
                "engine_a_pnl": current_engine_a_pnl,
                "engine_b_pnl": current_engine_b_pnl,
                "engine_a_shutoff": self._engine_a_shutoff_active,
                "engine_b_gross_target": self._last_engine_b_scale,
                "engine_b_max_score": self._last_engine_b_max_score,
                "engine_a_pnl_5d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_5d_ratio"]),
                "engine_a_pnl_21d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_21d_ratio"]),
                "engine_b_long_threshold_multiplier": float(self._last_engine_b_feedback["long_threshold_multiplier"]),
                "engine_b_short_threshold_multiplier": float(self._last_engine_b_feedback["short_threshold_multiplier"]),
                "engine_b_a_stress": bool(self._last_engine_b_feedback["a_stress"]),
                "engine_b_a_euphoria": bool(self._last_engine_b_feedback["a_euphoria"]),
                "engine_b_velocity_signal": engine_b_velocity_signal_mean,
                "engine_b_velocity_multiplier": engine_b_velocity_multiplier_mean,
                "engine_b_phase_mean": engine_b_phase_mean,
                "engine_b_phase3_count": engine_b_phase3_count,
                "engine_b_phase_skip_count": float(self._last_engine_b_phase_skip_count),
                "macro_surprise_confirm_count": macro_surprise_confirm_count,
                "macro_surprise_score_mean": macro_surprise_score_mean,
                "engine_b_phase_stop_count": engine_b_phase_stop_count,
                "engine_b_velocity_exit_count": engine_b_velocity_exit_count,
                "engine_b_convex_active_count": float(len(engine_b_convex_active)),
                "engine_b_convex_weight_mean": engine_b_convex_weight_mean,
                "engine_b_convex_gross": self._last_engine_b_convex_gross,
                "conditional_hedge_active_count": float(len(hedge_active)),
                "conditional_hedge_gross": self._last_conditional_hedge_gross,
                "trend_sleeve_active_count": float(len(trend_sleeve_active)),
                "trend_sleeve_gross": self._last_trend_sleeve_gross,
                "rates_curve_active_count": float(len(rates_curve_active)),
                "rates_curve_gross": self._last_rates_curve_gross,
                "rates_curve_futures_gross": self._last_rates_curve_futures_gross,
                "rates_curve_proxy_gross": self._last_rates_curve_proxy_gross,
                "rates_curve_futures_count": float(self._last_rates_curve_futures_count),
                "rates_curve_proxy_count": float(self._last_rates_curve_proxy_count),
                "rates_curve_zero_contract_fallback_count": float(
                    self._last_rates_curve_zero_contract_fallback_count
                ),
                "rates_curve_missing_market_count": float(self._last_rates_curve_missing_market_count),
                "rates_curve_trend_filter_count": float(self._last_rates_curve_trend_filter_count),
                "rates_ev_gate_block_count": float(self._last_rates_ev_gate_block_count),
                "rates_ev_gate_pass_count": float(self._last_rates_ev_gate_pass_count),
                "rates_ev_gate_ready_count": float(self._last_rates_ev_gate_ready_count),
                "rates_ev_gate_mean": (
                    self._last_rates_ev_gate_mean
                    / max(self._last_rates_ev_gate_block_count + self._last_rates_ev_gate_pass_count, 1)
                ),
                "curve_rv_active_count": float(len(curve_rv_active)),
                "curve_rv_gross": self._last_curve_rv_gross,
                "curve_rv_futures_gross": self._last_curve_rv_futures_gross,
                "curve_rv_proxy_gross": self._last_curve_rv_proxy_gross,
                "curve_rv_futures_count": float(self._last_curve_rv_futures_count),
                "curve_rv_proxy_count": float(self._last_curve_rv_proxy_count),
                "curve_rv_slope_z": self._last_curve_rv_slope_z,
                "curve_rv_slope_momentum": self._last_curve_rv_slope_momentum,
                "curve_rv_regime_score": self._last_curve_rv_regime_score,
                "commodity_futures_active_count": float(len(commodity_futures_active)),
                "commodity_futures_gross": self._last_commodity_futures_gross,
                "commodity_futures_missing_curve_count": float(
                    self._last_commodity_futures_reject_counts["missing_curve"]
                ),
                "commodity_futures_stale_curve_count": float(
                    self._last_commodity_futures_reject_counts["stale_curve"]
                ),
                "commodity_futures_weak_curve_count": float(
                    self._last_commodity_futures_reject_counts["weak_curve"]
                ),
                "commodity_futures_low_liquidity_count": float(
                    self._last_commodity_futures_reject_counts["low_liquidity"]
                ),
                "commodity_futures_weak_macro_count": float(
                    self._last_commodity_futures_reject_counts["weak_macro"]
                ),
                "commodity_futures_weak_momentum_count": float(
                    self._last_commodity_futures_reject_counts["weak_momentum"]
                ),
                "commodity_futures_momentum_mismatch_count": float(
                    self._last_commodity_futures_reject_counts["momentum_mismatch"]
                ),
                "commodity_futures_carry_mismatch_count": float(
                    self._last_commodity_futures_reject_counts["carry_mismatch"]
                ),
                "commodity_futures_price_only_count": float(
                    self._last_commodity_futures_reject_counts["price_only"]
                ),
                "crisis_trend_active_count": float(len(crisis_trend_active)),
                "crisis_trend_gross": self._last_crisis_trend_gross,
                "dollar_squeeze_active_count": float(len(dollar_squeeze_active)),
                "dollar_squeeze_gross": self._last_dollar_squeeze_gross,
                "liquidation_reversal_active_count": float(len(reversal_active)),
                "liquidation_reversal_gross": self._last_reversal_gross,
                "melt_up_reversal_active_count": float(len(melt_up_active)),
                "melt_up_reversal_gross": self._last_melt_up_gross,
                "engine_b_extreme_active": self._last_engine_b_extreme_active,
                "engine_b_extreme_coherence": self._last_engine_b_extreme_coherence,
                "engine_b_extreme_threshold": self._last_engine_b_extreme_threshold,
                "cross_asset_boost": self._last_cross_asset_boost,
                "cross_sleeve_amplifier_active": self._last_cross_sleeve_amplifier_active,
                "cross_sleeve_alignment_count": float(self._last_cross_sleeve_alignment_count),
                "cross_sleeve_direction": float(self._last_cross_sleeve_direction),
                "cross_sleeve_gross_multiplier": self._last_cross_sleeve_gross_multiplier,
                "vol_target_scalar": self._last_vol_target_scalar,
                "drawdown_throttle_scalar": self._last_drawdown_throttle_scalar,
                "portfolio_scalar": self._last_portfolio_scalar,
                "portfolio_vol_estimate": self._last_portfolio_vol_estimate,
                "nav_stop_count": float(self._last_nav_stop_count),
                "engine_a_reallocation_gross": self._last_engine_a_reallocation_gross,
                "engine_a_reallocation_count": float(self._last_engine_a_reallocation_count),
                "overlay_active_count": float(len(overlay_active)),
                "overlay_leader_mean": overlay_leader_mean,
                "overlay_lag_mean": overlay_lag_mean,
                "overlay_threshold_mean": overlay_threshold_mean,
                "overlay_weight_mean": overlay_weight_mean,
                "overlay_phase_mean": overlay_phase_mean,
                "overlay_phase_stop_count": overlay_phase_stop_count,
                "overlay_velocity_exit_count": overlay_velocity_exit_count,
                "emb_5d_z": float(self._last_engine_b_feedback.get("emb_5d_z", 0.0)),
                "emb_stress": bool(self._last_engine_b_feedback.get("emb_stress", False)),
                "emb_euphoria": bool(self._last_engine_b_feedback.get("emb_euphoria", False)),
            }
        )
        self._engine_a_pnl_5d_ratio_history.append(float(self._last_engine_b_feedback["engine_a_pnl_5d_ratio"]))
        episode = self._episode_label(as_of)
        for symbol in self.dislocation_symbols:
            plan = self._last_barbell_plans.get(symbol)
            bar = bars.get(symbol)
            if plan is None or bar is None or plan.engine != "ENGINE_B":
                continue
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            highs = np.fromiter(self._highs.get(symbol, deque()), dtype=float)
            lows = np.fromiter(self._lows.get(symbol, deque()), dtype=float)
            atr_value = _atr(highs, lows, closes, self.atr_period)
            realized_vol_21 = float(np.std(np.diff(closes[-22:]) / closes[-22:-1], ddof=0) * np.sqrt(252.0)) if len(closes) >= 22 else 0.0
            realized_vol_63 = float(np.std(np.diff(closes[-64:]) / closes[-64:-1], ddof=0) * np.sqrt(252.0)) if len(closes) >= 64 else 0.0
            trend_period = self._trend_period_for_symbol(
                symbol=symbol,
                asset_class=plan.asset_class,
                sign=1 if plan.score > 0 else -1 if plan.score < 0 else 0,
            )
            trend = _sma(closes, trend_period)
            trend_distance_atr = (
                float((float(bar.close) - trend) / max(atr_value or 0.0, 1e-9))
                if trend is not None and atr_value is not None
                else 0.0
            )
            macro_state = self._region_macro_state(plan.region, as_of)
            zscores = dict(macro_state["zscores"]) if macro_state is not None else {}
            side = 1 if plan.score > 0.0 else -1 if plan.score < 0.0 else 0
            row: dict[str, float | str | bool] = {
                "date": as_of.isoformat(),
                "episode": episode,
                "symbol": symbol,
                "sleeve": "ENGINE_B",
                "selected": bool(plan.core_target_weight != 0.0),
                "active_any": bool(plan.target_weight != 0.0),
                "side": float(side),
                "score": float(plan.score),
                "abs_score": float(abs(plan.score)),
                "target_weight": float(plan.target_weight),
                "core_target_weight": float(plan.core_target_weight),
                "rates_curve_target_weight": float(plan.rates_curve_target_weight),
                "curve_rv_target_weight": float(plan.curve_rv_target_weight),
                "reversal_target_weight": float(plan.reversal_target_weight),
                "phase": float(plan.core_phase),
                "reason": plan.reason,
                "region": plan.region,
                "theme": plan.theme,
                "asset_class": plan.asset_class,
                "coherence": float(plan.coherence),
                "velocity_signal": float(plan.velocity_signal),
                "macro_surprise_score": float(plan.macro_surprise_score),
                "macro_surprise_coherence": float(plan.macro_surprise_coherence),
                "macro_surprise_confirmed": bool(plan.macro_surprise_confirmed),
                "extreme_regime": bool(plan.extreme_regime),
                "cross_sleeve_amplifier": bool(plan.cross_sleeve_amplifier),
                "cross_sleeve_alignment_count": float(plan.cross_sleeve_alignment_count),
                "engine_a_shutoff": bool(self._engine_a_shutoff_active),
                "engine_a_pnl_5d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_5d_ratio"]),
                "engine_a_pnl_21d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_21d_ratio"]),
                "engine_b_a_stress": bool(self._last_engine_b_feedback["a_stress"]),
                "engine_b_a_euphoria": bool(self._last_engine_b_feedback["a_euphoria"]),
                "close": float(bar.close),
                "atr": float(atr_value or 0.0),
                "realized_vol_21": realized_vol_21,
                "realized_vol_63": realized_vol_63,
                "trend_period": float(trend_period),
                "trend_distance_atr": trend_distance_atr,
                "z_growth": float(zscores.get("growth", 0.0)),
                "z_inflation": float(zscores.get("inflation", 0.0)),
                "z_policy": float(zscores.get("policy", 0.0)),
                "z_liquidity": float(zscores.get("liquidity", 0.0)),
                "z_credit": float(zscores.get("credit", 0.0)),
            }
            row.update(self._factor_exposures(symbol=symbol, asset_class=plan.asset_class))
            self._candidate_research_rows.append(row)

        rates_symbols = tuple(dict.fromkeys((*self.rates_curve_symbols, *self.rates_proxy_symbols)))
        for symbol in rates_symbols:
            plan = self._last_barbell_plans.get(symbol)
            bar = bars.get(symbol)
            if (
                plan is None
                or bar is None
                or (plan.rates_curve_target_weight == 0.0 and plan.curve_rv_target_weight == 0.0)
            ):
                continue
            closes = np.fromiter(self._closes.get(symbol, deque()), dtype=float)
            highs = np.fromiter(self._highs.get(symbol, deque()), dtype=float)
            lows = np.fromiter(self._lows.get(symbol, deque()), dtype=float)
            atr_value = _atr(highs, lows, closes, self.atr_period)
            realized_vol_21 = (
                float(np.std(np.diff(closes[-22:]) / closes[-22:-1], ddof=0) * np.sqrt(252.0))
                if len(closes) >= 22
                else 0.0
            )
            realized_vol_63 = (
                float(np.std(np.diff(closes[-64:]) / closes[-64:-1], ddof=0) * np.sqrt(252.0))
                if len(closes) >= 64
                else 0.0
            )
            trend = _sma(closes, 50)
            trend_distance_atr = (
                float((float(bar.close) - trend) / max(atr_value or 0.0, 1e-9))
                if trend is not None and atr_value is not None
                else 0.0
            )
            macro_state = self._region_macro_state(plan.region, as_of)
            zscores = dict(macro_state["zscores"]) if macro_state is not None else {}
            sleeve_weight = (
                plan.rates_curve_target_weight
                if plan.rates_curve_target_weight != 0.0
                else plan.curve_rv_target_weight
            )
            side = 1 if sleeve_weight > 0.0 else -1
            row = {
                "date": as_of.isoformat(),
                "episode": episode,
                "symbol": symbol,
                "sleeve": "RATES_CURVE" if plan.rates_curve_target_weight != 0.0 else "CURVE_RV",
                "selected": True,
                "active_any": bool(plan.target_weight != 0.0),
                "side": float(side),
                "score": float(plan.score),
                "abs_score": float(abs(plan.score)),
                "target_weight": float(plan.target_weight),
                "core_target_weight": float(plan.core_target_weight),
                "rates_curve_target_weight": float(plan.rates_curve_target_weight),
                "curve_rv_target_weight": float(plan.curve_rv_target_weight),
                "reversal_target_weight": float(plan.reversal_target_weight),
                "phase": float(plan.core_phase),
                "reason": plan.rates_curve_reason or plan.curve_rv_reason or plan.reason,
                "region": plan.region,
                "theme": "RATES_CURVE" if plan.rates_curve_target_weight != 0.0 else "CURVE_RV",
                "asset_class": plan.asset_class,
                "coherence": float(plan.coherence),
                "velocity_signal": float(plan.velocity_signal),
                "macro_surprise_score": float(plan.macro_surprise_score),
                "macro_surprise_coherence": float(plan.macro_surprise_coherence),
                "macro_surprise_confirmed": bool(plan.macro_surprise_confirmed),
                "extreme_regime": bool(plan.extreme_regime),
                "cross_sleeve_amplifier": bool(plan.cross_sleeve_amplifier),
                "cross_sleeve_alignment_count": float(plan.cross_sleeve_alignment_count),
                "engine_a_shutoff": bool(self._engine_a_shutoff_active),
                "engine_a_pnl_5d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_5d_ratio"]),
                "engine_a_pnl_21d_ratio": float(self._last_engine_b_feedback["engine_a_pnl_21d_ratio"]),
                "engine_b_a_stress": bool(self._last_engine_b_feedback["a_stress"]),
                "engine_b_a_euphoria": bool(self._last_engine_b_feedback["a_euphoria"]),
                "close": float(bar.close),
                "atr": float(atr_value or 0.0),
                "realized_vol_21": realized_vol_21,
                "realized_vol_63": realized_vol_63,
                "trend_period": 50.0,
                "trend_distance_atr": trend_distance_atr,
                "z_growth": float(zscores.get("growth", 0.0)),
                "z_inflation": float(zscores.get("inflation", 0.0)),
                "z_policy": float(zscores.get("policy", 0.0)),
                "z_liquidity": float(zscores.get("liquidity", 0.0)),
                "z_credit": float(zscores.get("credit", 0.0)),
            }
            row.update(self._factor_exposures(symbol=symbol, asset_class=plan.asset_class))
            self._candidate_research_rows.append(row)

    def diagnostics(self) -> dict[str, object]:
        rows = pd.DataFrame(self._barbell_daily_rows)

        def summarize_trade_group(symbols: tuple[str, ...]) -> dict[str, float]:
            if self.portfolio is None:
                return {
                    "closed_trades": 0.0,
                    "net_pnl": 0.0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                }
            trades = [trade for trade in self.portfolio.closed_trades if trade.symbol in symbols]
            pnls = [float(trade.pnl) for trade in trades]
            wins = [pnl for pnl in pnls if pnl > 0]
            losses = [pnl for pnl in pnls if pnl < 0]
            profit_factor = (
                float(sum(wins) / abs(sum(losses)))
                if losses
                else (float("inf") if wins else 0.0)
            )
            return {
                "closed_trades": float(len(pnls)),
                "net_pnl": float(sum(pnls)),
                "win_rate": float(len(wins) / len(pnls)) if pnls else 0.0,
                "profit_factor": profit_factor,
            }

        out: dict[str, object] = {
            "carry_symbols": list(self.carry_symbols),
            "dislocation_symbols": list(self.dislocation_symbols),
            "sensor_symbols": list(self.sensor_symbols),
            "macro_sensor_series": list(self.macro_sensor_series),
            "rates_curve_symbols": list(self.rates_curve_symbols),
            "rates_proxy_symbols": list(self.rates_proxy_symbols),
            "commodity_futures_symbols": list(self.commodity_futures_symbols),
            "futures_curve_source_available": self.futures_curve_source is not None,
            "engine_a_last_trigger_reason": self._engine_a_last_trigger_reason,
            "engine_a_last_trigger_metrics": dict(self._engine_a_last_trigger_metrics),
            "engine_a_trade_summary": summarize_trade_group(self.carry_symbols),
            "engine_b_trade_summary": summarize_trade_group(self.dislocation_symbols),
        }
        if not rows.empty:
            out["engine_a_avg_gross"] = float(rows["engine_a_gross"].mean())
            out["engine_b_avg_gross"] = float(rows["engine_b_gross"].mean())
            out["engine_a_shutoff_ratio"] = float(rows["engine_a_shutoff"].mean())
            out["engine_b_dormant_ratio"] = float((rows["engine_b_gross_target"] <= 0).mean())
            out["engine_b_avg_max_score"] = float(rows["engine_b_max_score"].mean())
            out["engine_a_pnl_5d_ratio_mean"] = float(rows["engine_a_pnl_5d_ratio"].mean())
            out["engine_a_pnl_21d_ratio_mean"] = float(rows["engine_a_pnl_21d_ratio"].mean())
            out["engine_b_a_stress_ratio"] = float(rows["engine_b_a_stress"].mean())
            out["engine_b_a_euphoria_ratio"] = float(rows["engine_b_a_euphoria"].mean())
            out["engine_b_long_threshold_multiplier_mean"] = float(
                rows["engine_b_long_threshold_multiplier"].mean()
            )
            out["engine_b_short_threshold_multiplier_mean"] = float(
                rows["engine_b_short_threshold_multiplier"].mean()
            )
            out["engine_b_velocity_signal_mean"] = float(rows["engine_b_velocity_signal"].mean())
            out["engine_b_velocity_multiplier_mean"] = float(rows["engine_b_velocity_multiplier"].mean())
            out["engine_b_phase_mean"] = float(rows["engine_b_phase_mean"].mean())
            out["engine_b_phase3_ratio"] = float((rows["engine_b_phase3_count"] > 0).mean())
            out["engine_b_phase_skip_ratio"] = (
                float((rows["engine_b_phase_skip_count"] > 0).mean())
                if "engine_b_phase_skip_count" in rows
                else 0.0
            )
            out["engine_b_phase_skip_total"] = (
                float(rows["engine_b_phase_skip_count"].sum())
                if "engine_b_phase_skip_count" in rows
                else 0.0
            )
            out["macro_surprise_confirm_ratio"] = float((rows["macro_surprise_confirm_count"] > 0).mean())
            out["macro_surprise_score_mean"] = float(rows["macro_surprise_score_mean"].mean())
            out["engine_b_phase_stop_ratio"] = float((rows["engine_b_phase_stop_count"] > 0).mean())
            out["engine_b_velocity_exit_ratio"] = float((rows["engine_b_velocity_exit_count"] > 0).mean())
            out["engine_b_convex_active_ratio"] = float((rows["engine_b_convex_active_count"] > 0).mean())
            out["engine_b_convex_weight_mean"] = float(rows["engine_b_convex_weight_mean"].mean())
            out["engine_b_convex_gross_mean"] = float(rows["engine_b_convex_gross"].mean())
            out["conditional_hedge_active_ratio"] = float((rows["conditional_hedge_active_count"] > 0).mean())
            out["conditional_hedge_gross_mean"] = float(rows["conditional_hedge_gross"].mean())
            out["trend_sleeve_active_ratio"] = float((rows["trend_sleeve_active_count"] > 0).mean())
            out["trend_sleeve_gross_mean"] = float(rows["trend_sleeve_gross"].mean())
            out["rates_curve_active_ratio"] = float((rows["rates_curve_active_count"] > 0).mean())
            out["rates_curve_gross_mean"] = float(rows["rates_curve_gross"].mean())
            out["rates_curve_futures_gross_mean"] = float(rows["rates_curve_futures_gross"].mean())
            out["rates_curve_proxy_gross_mean"] = float(rows["rates_curve_proxy_gross"].mean())
            out["rates_curve_futures_active_ratio"] = float((rows["rates_curve_futures_count"] > 0).mean())
            out["rates_curve_proxy_active_ratio"] = float((rows["rates_curve_proxy_count"] > 0).mean())
            out["rates_curve_zero_contract_fallback_total"] = float(
                rows["rates_curve_zero_contract_fallback_count"].sum()
            )
            out["rates_curve_missing_market_total"] = float(rows["rates_curve_missing_market_count"].sum())
            out["rates_curve_trend_filter_total"] = float(rows["rates_curve_trend_filter_count"].sum())
            out["rates_ev_gate_block_total"] = float(rows["rates_ev_gate_block_count"].sum())
            out["rates_ev_gate_pass_total"] = float(rows["rates_ev_gate_pass_count"].sum())
            out["rates_ev_gate_ready_total"] = float(rows["rates_ev_gate_ready_count"].sum())
            out["rates_ev_gate_mean"] = float(rows["rates_ev_gate_mean"].mean())
            out["curve_rv_active_ratio"] = float((rows["curve_rv_active_count"] > 0).mean())
            out["curve_rv_gross_mean"] = float(rows["curve_rv_gross"].mean())
            out["curve_rv_futures_gross_mean"] = float(rows["curve_rv_futures_gross"].mean())
            out["curve_rv_proxy_gross_mean"] = float(rows["curve_rv_proxy_gross"].mean())
            out["curve_rv_futures_active_ratio"] = float((rows["curve_rv_futures_count"] > 0).mean())
            out["curve_rv_proxy_active_ratio"] = float((rows["curve_rv_proxy_count"] > 0).mean())
            out["curve_rv_slope_z_mean"] = float(rows["curve_rv_slope_z"].mean())
            out["curve_rv_slope_momentum_mean"] = float(rows["curve_rv_slope_momentum"].mean())
            out["curve_rv_regime_score_mean"] = float(rows["curve_rv_regime_score"].mean())
            out["commodity_futures_active_ratio"] = float((rows["commodity_futures_active_count"] > 0).mean())
            out["commodity_futures_gross_mean"] = float(rows["commodity_futures_gross"].mean())
            out["commodity_futures_missing_curve_total"] = float(rows["commodity_futures_missing_curve_count"].sum())
            out["commodity_futures_stale_curve_total"] = float(rows["commodity_futures_stale_curve_count"].sum())
            out["commodity_futures_weak_curve_total"] = float(rows["commodity_futures_weak_curve_count"].sum())
            out["commodity_futures_low_liquidity_total"] = float(
                rows["commodity_futures_low_liquidity_count"].sum()
            )
            out["commodity_futures_weak_macro_total"] = float(rows["commodity_futures_weak_macro_count"].sum())
            out["commodity_futures_weak_momentum_total"] = float(
                rows["commodity_futures_weak_momentum_count"].sum()
            )
            out["commodity_futures_momentum_mismatch_total"] = float(
                rows["commodity_futures_momentum_mismatch_count"].sum()
            )
            out["commodity_futures_carry_mismatch_total"] = float(
                rows["commodity_futures_carry_mismatch_count"].sum()
            )
            out["commodity_futures_price_only_total"] = float(rows["commodity_futures_price_only_count"].sum())
            out["crisis_trend_active_ratio"] = float((rows["crisis_trend_active_count"] > 0).mean())
            out["crisis_trend_gross_mean"] = float(rows["crisis_trend_gross"].mean())
            out["dollar_squeeze_active_ratio"] = float((rows["dollar_squeeze_active_count"] > 0).mean())
            out["dollar_squeeze_gross_mean"] = float(rows["dollar_squeeze_gross"].mean())
            out["liquidation_reversal_active_ratio"] = float((rows["liquidation_reversal_active_count"] > 0).mean())
            out["liquidation_reversal_gross_mean"] = float(rows["liquidation_reversal_gross"].mean())
            out["melt_up_reversal_active_ratio"] = float((rows["melt_up_reversal_active_count"] > 0).mean())
            out["melt_up_reversal_gross_mean"] = float(rows["melt_up_reversal_gross"].mean())
            out["engine_b_extreme_active_ratio"] = float(rows["engine_b_extreme_active"].mean())
            out["engine_b_extreme_coherence_mean"] = float(rows["engine_b_extreme_coherence"].mean())
            out["engine_b_extreme_threshold_mean"] = float(rows["engine_b_extreme_threshold"].mean())
            out["cross_asset_boost_mean"] = float(rows["cross_asset_boost"].mean())
            out["cross_sleeve_amplifier_active_ratio"] = float(rows["cross_sleeve_amplifier_active"].mean())
            out["cross_sleeve_alignment_count_mean"] = float(rows["cross_sleeve_alignment_count"].mean())
            out["cross_sleeve_gross_multiplier_mean"] = float(rows["cross_sleeve_gross_multiplier"].mean())
            out["vol_target_scalar_mean"] = float(rows["vol_target_scalar"].mean())
            out["drawdown_throttle_scalar_mean"] = float(rows["drawdown_throttle_scalar"].mean())
            out["portfolio_scalar_mean"] = float(rows["portfolio_scalar"].mean())
            out["portfolio_vol_estimate_mean"] = float(rows["portfolio_vol_estimate"].mean())
            out["nav_stop_ratio"] = float((rows["nav_stop_count"] > 0).mean())
            out["engine_a_reallocation_active_ratio"] = float((rows["engine_a_reallocation_gross"] > 0).mean())
            out["engine_a_reallocation_gross_mean"] = float(rows["engine_a_reallocation_gross"].mean())
            out["engine_a_reallocation_avg_active_count"] = float(rows["engine_a_reallocation_count"].mean())
            out["overlay_active_ratio"] = float((rows["overlay_active_count"] > 0).mean())
            out["overlay_avg_active_count"] = float(rows["overlay_active_count"].mean())
            out["overlay_leader_mean"] = float(rows["overlay_leader_mean"].mean())
            out["overlay_lag_mean"] = float(rows["overlay_lag_mean"].mean())
            out["overlay_threshold_mean"] = float(rows["overlay_threshold_mean"].mean())
            out["overlay_weight_mean"] = float(rows["overlay_weight_mean"].mean())
            out["overlay_phase_mean"] = float(rows["overlay_phase_mean"].mean())
            out["overlay_phase_stop_ratio"] = float((rows["overlay_phase_stop_count"] > 0).mean())
            out["overlay_velocity_exit_ratio"] = float((rows["overlay_velocity_exit_count"] > 0).mean())
            out["emb_stress_ratio"] = float(rows["emb_stress"].mean()) if "emb_stress" in rows else 0.0
            out["emb_euphoria_ratio"] = float(rows["emb_euphoria"].mean()) if "emb_euphoria" in rows else 0.0
            out["engine_a_final_pnl"] = float(rows["engine_a_pnl"].iloc[-1])
            out["engine_b_final_pnl"] = float(rows["engine_b_pnl"].iloc[-1])
        out["engine_a_reallocation_enabled"] = self.enable_engine_a_reallocation_layer
        out["engine_a_focused_reallocation_enabled"] = self.enable_focused_reallocation_layer
        out["engine_b_asymmetric_phase_enabled"] = self.enable_asymmetric_phase_timing_layer
        out["engine_b_extreme_concentration_enabled"] = self.enable_extreme_concentration_layer
        out["engine_b_phase3_profit_rate_enabled"] = self.enable_phase3_profit_rate_layer
        out["overlay_routing_enabled"] = self.enable_overlay_routing_layer
        out["overlay_off"] = self.overlay_off
        out["engine_b_convex_dislocation_enabled"] = self.enable_convex_dislocation_layer
        out["conditional_hedge_enabled"] = self.enable_conditional_hedge_layer
        out["time_series_momentum_enabled"] = self.enable_time_series_momentum_layer
        out["rates_curve_enabled"] = self.enable_rates_curve_layer
        out["rates_rolling_ev_gate_enabled"] = self.enable_rates_rolling_ev_gate_layer
        out["curve_rv_enabled"] = self.enable_curve_rv_layer
        out["commodity_futures_sleeve_enabled"] = self.enable_commodity_futures_sleeve_layer
        out["crisis_trend_enabled"] = self.enable_crisis_trend_layer
        out["dollar_squeeze_enabled"] = self.enable_dollar_squeeze_layer
        out["liquidation_reversal_enabled"] = self.enable_liquidation_reversal_layer
        out["optimization_enabled"] = self.enable_optimization_layer
        out["portfolio_vol_targeting_enabled"] = self.enable_portfolio_vol_targeting_layer
        out["adaptive_threshold_enabled"] = self.enable_adaptive_threshold_layer
        out["correlation_selection_enabled"] = self.enable_correlation_selection_layer
        out["asymmetric_trend_filter_enabled"] = self.enable_asymmetric_trend_filter_layer
        out["dynamic_engine_a_enabled"] = self.enable_dynamic_engine_a_layer
        out["phase4_enabled"] = self.enable_phase4_layer
        out["cross_asset_confirmation_enabled"] = self.enable_cross_asset_confirmation_layer
        out["cross_sleeve_amplifier_enabled"] = self.enable_cross_sleeve_amplifier_layer
        out["macro_surprise_enabled"] = self.enable_macro_surprise_layer
        out["market_evidence_enabled"] = self.enable_market_evidence_layer
        out["engine_b_core_enabled"] = self.enable_engine_b_core
        out["cheap_discovery_enabled"] = self.enable_cheap_discovery_layer
        out["macro_inputs_are_zscores"] = self.macro_inputs_are_zscores
        out["extreme_phase_skip_enabled"] = self.enable_extreme_phase_skip_layer
        out["melt_up_reversal_enabled"] = self.enable_melt_up_reversal_layer
        out["carry_crash_risk_enabled"] = self.enable_carry_crash_risk_layer
        out["drawdown_throttle_enabled"] = self.enable_drawdown_throttle_layer
        out["nav_stop_enabled"] = self.enable_nav_stop_layer
        return out

    def candidate_research_frame(self) -> pd.DataFrame:
        if not self._candidate_research_rows:
            return pd.DataFrame()
        frame = pd.DataFrame(self._candidate_research_rows)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"])
        frame["candidate_abs_score_rank"] = frame.groupby("date")["abs_score"].rank(
            method="first",
            ascending=False,
        )
        horizons = (5, 10, 21, 63)
        for horizon in horizons:
            frame[f"fwd_return_{horizon}d"] = np.nan
            frame[f"side_fwd_return_{horizon}d"] = np.nan
            frame[f"vol_norm_side_fwd_return_{horizon}d"] = np.nan
            frame[f"mae_{horizon}d"] = np.nan
            frame[f"mfe_{horizon}d"] = np.nan
            frame[f"side_mae_{horizon}d"] = np.nan
            frame[f"side_mfe_{horizon}d"] = np.nan

        for symbol, group_index in frame.groupby("symbol").groups.items():
            closes = list(self._research_closes.get(symbol, []))
            dates = list(self._research_dates.get(symbol, []))
            if len(closes) < 2 or len(closes) != len(dates):
                continue
            series = pd.Series(closes, index=pd.to_datetime(dates), dtype=float)
            # Keep the final bar for each date if an aligned data source repeats dates.
            series = series.groupby(series.index).last()
            date_to_pos = {ts: idx for idx, ts in enumerate(series.index)}
            values = series.to_numpy(dtype=float)
            for row_idx in group_index:
                ts = pd.Timestamp(frame.at[row_idx, "date"])
                pos = date_to_pos.get(ts)
                if pos is None:
                    continue
                entry = float(values[pos])
                if entry <= 0.0:
                    continue
                side = float(frame.at[row_idx, "side"])
                realized_vol = float(frame.at[row_idx, "realized_vol_21"] or 0.0)
                for horizon in horizons:
                    end_pos = pos + horizon
                    if end_pos >= len(values):
                        continue
                    fwd = float(values[end_pos] / entry - 1.0)
                    path = values[pos + 1 : end_pos + 1] / entry - 1.0
                    if len(path) == 0:
                        continue
                    side_path = path * side
                    horizon_vol = max(realized_vol * np.sqrt(horizon / 252.0), 1e-9)
                    frame.at[row_idx, f"fwd_return_{horizon}d"] = fwd
                    frame.at[row_idx, f"side_fwd_return_{horizon}d"] = side * fwd
                    frame.at[row_idx, f"vol_norm_side_fwd_return_{horizon}d"] = side * fwd / horizon_vol
                    frame.at[row_idx, f"mae_{horizon}d"] = float(np.min(path))
                    frame.at[row_idx, f"mfe_{horizon}d"] = float(np.max(path))
                    frame.at[row_idx, f"side_mae_{horizon}d"] = float(np.min(side_path))
                    frame.at[row_idx, f"side_mfe_{horizon}d"] = float(np.max(side_path))
        return frame

    def trade_attribution(self) -> dict[str, object]:
        if self.portfolio is None:
            return {}

        def episode_label(ts: pd.Timestamp) -> str:
            if pd.Timestamp("2007-01-01") <= ts <= pd.Timestamp("2009-12-31"):
                return "crisis_2007_2009"
            if pd.Timestamp("2014-01-01") <= ts <= pd.Timestamp("2016-12-31"):
                return "transition_2014_2016"
            if pd.Timestamp("2020-01-01") <= ts <= pd.Timestamp("2020-12-31"):
                return "shock_2020"
            if pd.Timestamp("2022-01-01") <= ts <= pd.Timestamp("2022-12-31"):
                return "shock_2022"
            return "all_other_periods"

        rows: list[dict[str, object]] = []
        for trade in self.portfolio.closed_trades:
            if trade.symbol not in self.symbols:
                continue
            meta = dict(trade.metadata)
            exit_ts = pd.Timestamp(trade.exit_timestamp or trade.timestamp)
            rows.append(
                {
                    "symbol": trade.symbol,
                    "pnl": float(trade.pnl),
                    "quantity": float(trade.quantity),
                    "entry_timestamp": trade.entry_timestamp.isoformat() if trade.entry_timestamp else None,
                    "exit_timestamp": (trade.exit_timestamp or trade.timestamp).isoformat(),
                    "days_held": int(meta.get("days_held", 0) or 0),
                    "direction": int(trade.direction),
                    "direction_label": meta.get("direction_label", "LONG" if trade.direction > 0 else "SHORT"),
                    "strategy": meta.get("strategy"),
                    "engine": meta.get("engine"),
                    "entry_reason": meta.get("entry_reason"),
                    "exit_reason": meta.get("exit_reason"),
                    "region": meta.get("entry_region"),
                    "theme": meta.get("entry_theme"),
                    "asset_class": meta.get("entry_asset_class"),
                    "entry_core_phase": int(meta.get("entry_core_phase", 0) or 0),
                    "entry_overlay_phase": int(meta.get("entry_overlay_phase", 0) or 0),
                    "max_core_phase": int(meta.get("max_core_phase", 0) or 0),
                    "max_overlay_phase": int(meta.get("max_overlay_phase", 0) or 0),
                    "max_abs_target_weight": float(meta.get("max_abs_target_weight", 0.0) or 0.0),
                    "max_abs_score": float(meta.get("max_abs_score", 0.0) or 0.0),
                    "max_coherence": float(meta.get("max_coherence", 0.0) or 0.0),
                    "max_velocity_signal": float(meta.get("max_velocity_signal", 0.0) or 0.0),
                    "extreme_regime_seen": bool(meta.get("extreme_regime_seen", False)),
                    "reallocation_seen": bool(meta.get("reallocation_seen", False)),
                    "phase_1_days": int(meta.get("phase_1_days", 0) or 0),
                    "phase_2_days": int(meta.get("phase_2_days", 0) or 0),
                    "phase_3_days": int(meta.get("phase_3_days", 0) or 0),
                    "overlay_phase_1_days": int(meta.get("overlay_phase_1_days", 0) or 0),
                    "overlay_phase_2_days": int(meta.get("overlay_phase_2_days", 0) or 0),
                    "overlay_phase_3_days": int(meta.get("overlay_phase_3_days", 0) or 0),
                    "extreme_regime_days": int(meta.get("extreme_regime_days", 0) or 0),
                    "reallocation_days": int(meta.get("reallocation_days", 0) or 0),
                    "episode_exit": episode_label(exit_ts),
                }
            )

        if not rows:
            return {}

        frame = pd.DataFrame(rows)
        frame["phase2plus_participated"] = (frame["phase_2_days"] + frame["phase_3_days"]) > 0
        frame["phase3_participated"] = frame["phase_3_days"] > 0
        frame["reallocation_participated"] = frame["reallocation_days"] > 0
        frame["extreme_regime_participated"] = frame["extreme_regime_days"] > 0

        def summarize(group: pd.DataFrame) -> dict[str, float]:
            pnls = group["pnl"].astype(float)
            wins = pnls[pnls > 0]
            losses = pnls[pnls < 0]
            gross_profit = float(wins.sum())
            gross_loss = float(losses.sum())
            profit_factor = (
                gross_profit / abs(gross_loss)
                if gross_loss < 0
                else (float("inf") if gross_profit > 0 else 0.0)
            )
            return {
                "closed_trades": float(len(group)),
                "net_pnl": float(pnls.sum()),
                "win_rate": float((pnls > 0).mean()) if len(group) else 0.0,
                "profit_factor": float(profit_factor),
                "avg_days_held": float(group["days_held"].mean()) if len(group) else 0.0,
                "avg_abs_target_weight": float(group["max_abs_target_weight"].mean()) if len(group) else 0.0,
            }

        def grouped_summary(column: str) -> dict[str, dict[str, float]]:
            out: dict[str, dict[str, float]] = {}
            for key, group in frame.groupby(column, dropna=False):
                label = str(key)
                out[label] = summarize(group)
            return out

        episode_symbol_summary: dict[str, dict[str, dict[str, float]]] = {}
        for episode, episode_group in frame.groupby("episode_exit", dropna=False):
            symbol_map: dict[str, dict[str, float]] = {}
            for symbol, symbol_group in episode_group.groupby("symbol", dropna=False):
                symbol_map[str(symbol)] = summarize(symbol_group)
            episode_symbol_summary[str(episode)] = symbol_map

        return {
            "episode_basis": "exit_timestamp",
            "closed_trade_count": int(len(frame)),
            "rows": rows,
            "by_engine": grouped_summary("engine"),
            "by_symbol": grouped_summary("symbol"),
            "by_episode_exit": grouped_summary("episode_exit"),
            "by_max_core_phase": grouped_summary("max_core_phase"),
            "by_exit_reason": grouped_summary("exit_reason"),
            "by_phase2plus_participation": grouped_summary("phase2plus_participated"),
            "by_phase3_participation": grouped_summary("phase3_participated"),
            "by_reallocation_participation": grouped_summary("reallocation_participated"),
            "by_extreme_regime_participation": grouped_summary("extreme_regime_participated"),
            "episode_symbol_summary": episode_symbol_summary,
        }

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        self._bar_index += 1
        self._last_engine_b_phase_skip_count = 0
        as_of = market_event.timestamp.date()
        sector_map = self.security_master.sector_map(as_of)
        region_map = self.security_master.region_map(as_of)
        asset_map = self.security_master.asset_class_map(as_of)

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            self._dates[symbol].append(as_of)
            self._research_closes[symbol].append(bar.close)
            self._research_dates[symbol].append(as_of)

        self._sync_position_state(market_event)

        (
            region_states,
            region_snapshots,
            region_themes,
            _region_velocities,
            region_velocity_signals,
            _region_velocity_alignments,
        ) = self._region_context(as_of=as_of, region_map=region_map)
        equity = max(float(self.portfolio.latest_equity), 1e-9)
        _, engine_a_state_changed = self._update_engine_a_state(
            as_of=as_of,
            region_states=region_states,
            bars=market_event.bars,
            equity=equity,
        )

        blocked_symbols: set[str] = set()
        if not self.enable_phase_stop_layer:
            for symbol in self.dislocation_symbols:
                bar = market_event.bars.get(symbol)
                if bar is None:
                    continue
                highs = np.fromiter(self._highs[symbol], dtype=float)
                lows = np.fromiter(self._lows[symbol], dtype=float)
                closes = np.fromiter(self._closes[symbol], dtype=float)
                atr = _atr(highs, lows, closes, self.atr_period)
                if atr is None:
                    continue
                qty = int(self.portfolio.position_for_symbol(symbol).quantity)
                if qty > 0:
                    stop_level = self._high_water.get(symbol, bar.high) - self.trailing_atr_mult * atr
                    if bar.close < stop_level:
                        blocked_symbols.add(symbol)
                        plan = BarbellTargetPlan(
                            symbol=symbol,
                            engine="ENGINE_B",
                            target_weight=0.0,
                            score=0.0,
                            region=region_map.get(symbol, "GLOBAL"),
                            theme="STOP",
                            asset_class=asset_map.get(symbol, "EQUITY"),
                            reason="TRAILING_STOP",
                        )
                        signal = self._emit_barbell_delta(
                            market_event=market_event,
                            plan=plan,
                            current_qty=qty,
                            desired_qty=0,
                        )
                        if signal is not None:
                            signals.append(signal)
                elif qty < 0:
                    stop_level = self._low_water.get(symbol, bar.low) + self.trailing_atr_mult * atr
                    if bar.close > stop_level:
                        blocked_symbols.add(symbol)
                        plan = BarbellTargetPlan(
                            symbol=symbol,
                            engine="ENGINE_B",
                            target_weight=0.0,
                            score=0.0,
                            region=region_map.get(symbol, "GLOBAL"),
                            theme="STOP",
                            asset_class=asset_map.get(symbol, "EQUITY"),
                            reason="TRAILING_STOP",
                        )
                        signal = self._emit_barbell_delta(
                            market_event=market_event,
                            plan=plan,
                            current_qty=qty,
                            desired_qty=0,
                        )
                        if signal is not None:
                            signals.append(signal)

        prices = {
            symbol: market_event.bars[symbol].close
            for symbol in self.symbols
            if symbol in market_event.bars
        }
        weekly_resize = self._needs_weekly_resize(as_of)
        core_refresh = weekly_resize or engine_a_state_changed or not self._core_barbell_plans
        if core_refresh:
            self._core_barbell_plans = self._build_barbell_plans(
                as_of=as_of,
                sector_map=sector_map,
                region_map=region_map,
                asset_map=asset_map,
                region_states=region_states,
                region_snapshots=region_snapshots,
                region_themes=region_themes,
                region_velocity_signals=region_velocity_signals,
                market_event=market_event,
            )
        plans = self._apply_transmission_overlay(
            plans=self._core_barbell_plans,
            market_event=market_event,
            prices=prices,
            equity=equity,
        )
        phase_blocked: set[str] = set()
        if self.enable_phase_stop_layer:
            plans, phase_blocked = self._apply_phase_stop_architecture(plans=plans, market_event=market_event)
        plans = self._apply_velocity_exits(plans=plans)
        plans = self._apply_cross_asset_confirmation(plans=plans)
        plans = self._apply_convex_dislocation_sleeve(plans=plans)
        plans = self._apply_conditional_hedge_overlay(
            plans=plans,
            region_states=region_states,
            market_event=market_event,
        )
        plans = self._apply_time_series_momentum_sleeve(plans=plans, as_of=as_of)
        plans = self._apply_rates_curve_sleeve(
            plans=plans,
            region_states=region_states,
            market_event=market_event,
        )
        plans = self._apply_curve_rv_sleeve(
            plans=plans,
            region_states=region_states,
            market_event=market_event,
        )
        plans = self._apply_commodity_futures_sleeve(
            plans=plans,
            region_states=region_states,
            market_event=market_event,
        )
        plans = self._apply_engine_a_shutoff_reallocation(plans=plans)
        plans = self._apply_crisis_trend_sleeve(plans=plans)
        plans = self._apply_dollar_squeeze_sleeve(plans=plans)
        plans = self._apply_liquidation_reversal_sleeve(plans=plans)
        plans = self._apply_melt_up_reversal_sleeve(plans=plans)
        plans = self._apply_portfolio_scalars(plans=plans, equity=equity)
        plans = self._apply_nav_stops(plans=plans, market_event=market_event, equity=equity)
        blocked_symbols.update(phase_blocked)
        self._last_barbell_plans = self._clone_barbell_plans(plans)

        projected_positions = {
            symbol: int(self.portfolio.position_for_symbol(symbol).quantity)
            for symbol in self.symbols
        }
        reductions: list[BarbellTargetCandidate] = []
        increases: list[BarbellTargetCandidate] = []
        for symbol in self.symbols:
            if symbol in blocked_symbols:
                continue
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            plan = plans.get(
                symbol,
                BarbellTargetPlan(
                    symbol=symbol,
                    engine="UNASSIGNED",
                    target_weight=0.0,
                    score=0.0,
                    region=region_map.get(symbol, "GLOBAL"),
                    theme="NONE",
                    asset_class=asset_map.get(symbol, "EQUITY"),
                    reason="UNUSED",
                ),
            )
            overlay_adjust = bool(
                plan.engine == "ENGINE_B"
                and (
                    plan.overlay_target_weight != 0.0
                    or bool(plan.overlay_reason)
                    or symbol in self._overlay_state
                )
            )
            velocity_adjust = bool(plan.engine == "ENGINE_B" and plan.reason == "ENGINE_B_VELOCITY_EXIT")
            phase_adjust = bool(
                self.enable_phase_stop_layer
                and plan.engine == "ENGINE_B"
                and (
                    symbol in self._engine_b_state
                    or plan.core_phase != 0
                    or plan.reason == "ENGINE_B_PHASE_STOP"
                    or plan.overlay_reason in {"ENGINE_B_OVERLAY_PHASE_STOP", "ENGINE_B_OVERLAY_PHASE_EXIT"}
                )
            )
            research_adjust = bool(
                (
                    plan.engine == "ENGINE_B"
                    or plan.convex_target_weight != 0.0
                    or plan.hedge_target_weight != 0.0
                    or plan.trend_sleeve_target_weight != 0.0
                    or plan.rates_curve_target_weight != 0.0
                    or plan.curve_rv_target_weight != 0.0
                    or plan.commodity_futures_target_weight != 0.0
                    or plan.crisis_trend_target_weight != 0.0
                    or plan.dollar_squeeze_target_weight != 0.0
                    or plan.reversal_target_weight != 0.0
                    or plan.melt_up_target_weight != 0.0
                    or (
                        (self.enable_rates_curve_layer or self.enable_curve_rv_layer)
                        and (symbol in self.rates_curve_symbols or symbol in {"^FVX", "^TNX", "ZF=F", "ZN=F"})
                    )
                    or (
                        self.enable_commodity_futures_sleeve_layer
                        and symbol in self.commodity_futures_symbols
                    )
                )
                and (
                    plan.convex_target_weight != 0.0
                    or plan.hedge_target_weight != 0.0
                    or plan.trend_sleeve_target_weight != 0.0
                    or plan.rates_curve_target_weight != 0.0
                    or plan.curve_rv_target_weight != 0.0
                    or plan.commodity_futures_target_weight != 0.0
                    or plan.crisis_trend_target_weight != 0.0
                    or plan.dollar_squeeze_target_weight != 0.0
                    or plan.reversal_target_weight != 0.0
                    or plan.melt_up_target_weight != 0.0
                    or plan.reason == "POSITION_NAV_STOP"
                    or (self.enable_conditional_hedge_layer and symbol in self.hedge_symbols)
                    or (
                        (
                            self.enable_crisis_trend_layer
                            or self.enable_dollar_squeeze_layer
                            or self.enable_liquidation_reversal_layer
                            or self.enable_melt_up_reversal_layer
                            or self.enable_conditional_hedge_layer
                            or self.enable_time_series_momentum_layer
                            or self.enable_rates_curve_layer
                            or self.enable_curve_rv_layer
                            or self.enable_commodity_futures_sleeve_layer
                            or self.enable_portfolio_vol_targeting_layer
                            or self.enable_drawdown_throttle_layer
                            or self.enable_nav_stop_layer
                        )
                        and (
                            symbol in self.dislocation_symbols
                            or symbol in self.rates_curve_symbols
                            or symbol in {"^FVX", "^TNX", "ZF=F", "ZN=F"}
                            or symbol in self.commodity_futures_symbols
                        )
                    )
                )
            )
            if not core_refresh and not overlay_adjust and not velocity_adjust and not phase_adjust and not research_adjust:
                continue
            current_qty = int(self.portfolio.position_for_symbol(symbol).quantity)
            target_qty = self._barbell_target_quantity(plan=plan, price=bar.close, current_qty=current_qty)
            if current_qty == target_qty:
                continue
            candidate = BarbellTargetCandidate(
                symbol=symbol,
                engine=plan.engine,
                desired_qty=target_qty,
                price=bar.close,
                score=plan.score,
                region=plan.region,
                asset_class=plan.asset_class,
                theme=plan.theme,
                reason=plan.reason,
            )
            if abs(target_qty) < abs(current_qty):
                reductions.append(candidate)
            else:
                increases.append(candidate)

        for candidate in reductions:
            current_qty = int(projected_positions.get(candidate.symbol, 0))
            plan = plans[candidate.symbol]
            signal = self._emit_barbell_delta(
                market_event=market_event,
                plan=plan,
                current_qty=current_qty,
                desired_qty=candidate.desired_qty,
            )
            if signal is None:
                continue
            signals.append(signal)
            projected_positions[candidate.symbol] = candidate.desired_qty

        for candidate in sorted(increases, key=lambda item: abs(item.score), reverse=True):
            current_qty = int(projected_positions.get(candidate.symbol, 0))
            fitted_qty = self._fit_target_qty(
                symbol=candidate.symbol,
                desired_qty=candidate.desired_qty,
                projected_positions=projected_positions,
                prices=prices,
                region_map=region_map,
                asset_map=asset_map,
                equity=equity,
                convex_override=plans[candidate.symbol].convex_target_weight != 0.0,
            )
            if fitted_qty == current_qty:
                continue
            plan = plans[candidate.symbol]
            signal = self._emit_barbell_delta(
                market_event=market_event,
                plan=plan,
                current_qty=current_qty,
                desired_qty=fitted_qty,
            )
            if signal is None:
                continue
            signals.append(signal)
            projected_positions[candidate.symbol] = fitted_qty

        if weekly_resize and not self._engine_a_shutoff_active and self._engine_a_reentry_step is not None:
            self._engine_a_reentry_step = min(self._engine_a_reentry_step + 1, self.carry_reentry_weeks)
            if self._engine_a_reentry_step >= self.carry_reentry_weeks:
                self._engine_a_reentry_step = None

        self._sync_barbell_trade_context(
            market_event=market_event,
            plans=plans,
            projected_positions=projected_positions,
            prices=prices,
            equity=equity,
        )
        self._sync_overlay_state(
            plans=plans,
            projected_positions=projected_positions,
            prices=prices,
            equity=equity,
        )
        self._sync_engine_b_state(plans=plans, projected_positions=projected_positions, prices=prices)
        self._record_barbell_daily(as_of=as_of, bars=market_event.bars, equity=equity)
        return signals


class RelativeValueStatArbWizardStrategy(BaseStrategy):
    """
    Market Wizards adaptation: market-neutral pair trades on standardized spread dislocations.
    """

    def __init__(
        self,
        pairs: list[PairSpec],
        lookback: int = 60,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        stop_z: float = 3.5,
        min_corr: float = 0.65,
        pair_risk_pct: float = 0.01,
        max_notional_pct: float = 0.20,
        time_stop_bars: int = 20,
    ) -> None:
        symbols = sorted({symbol for pair in pairs for symbol in (pair.left, pair.right)})
        super().__init__(symbols=symbols)
        self.pairs = pairs
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.min_corr = min_corr
        self.pair_risk_pct = pair_risk_pct
        self.max_notional_pct = max_notional_pct
        self.time_stop_bars = time_stop_bars
        self._closes = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}
        self._pair_age = {pair.key: 0 for pair in pairs}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is not None:
                self._closes[symbol].append(bar.close)

        for pair in self.pairs:
            left_bar = market_event.bars.get(pair.left)
            right_bar = market_event.bars.get(pair.right)
            if left_bar is None or right_bar is None:
                continue
            left_closes = np.fromiter(self._closes[pair.left], dtype=float)
            right_closes = np.fromiter(self._closes[pair.right], dtype=float)
            if len(left_closes) < self.lookback or len(right_closes) < self.lookback:
                continue

            x = np.log(left_closes[-self.lookback :])
            y = np.log(right_closes[-self.lookback :])
            corr = float(np.corrcoef(x, y)[0, 1])
            if not np.isfinite(corr):
                continue
            beta = float(np.cov(y, x, ddof=0)[0, 1] / max(np.var(x), 1e-9))
            spread = y - beta * x
            spread_std = float(np.std(spread, ddof=0))
            if spread_std <= 0:
                continue
            z = float((spread[-1] - np.mean(spread)) / spread_std)
            left_qty = float(self.portfolio.position_for_symbol(pair.left).quantity)
            right_qty = float(self.portfolio.position_for_symbol(pair.right).quantity)
            is_open = abs(left_qty) > 0 or abs(right_qty) > 0

            if is_open:
                self._pair_age[pair.key] += 1
                should_exit = (
                    abs(z) <= self.exit_z
                    or abs(z) >= self.stop_z
                    or self._pair_age[pair.key] >= self.time_stop_bars
                    or corr < self.min_corr * 0.8
                )
                if should_exit:
                    if left_qty > 0:
                        signals.append(
                            self.sell_moc(
                                timestamp=market_event.timestamp,
                                symbol=pair.left,
                                quantity=int(abs(left_qty)),
                                metadata={"strategy": "RELATIVE_VALUE", "reason": "SPREAD_EXIT"},
                            )
                        )
                    elif left_qty < 0:
                        signals.append(
                            self.buy_moc(
                                timestamp=market_event.timestamp,
                                symbol=pair.left,
                                quantity=int(abs(left_qty)),
                                metadata={"strategy": "RELATIVE_VALUE", "reason": "SPREAD_EXIT"},
                            )
                        )
                    if right_qty > 0:
                        signals.append(
                            self.sell_moc(
                                timestamp=market_event.timestamp,
                                symbol=pair.right,
                                quantity=int(abs(right_qty)),
                                metadata={"strategy": "RELATIVE_VALUE", "reason": "SPREAD_EXIT"},
                            )
                        )
                    elif right_qty < 0:
                        signals.append(
                            self.buy_moc(
                                timestamp=market_event.timestamp,
                                symbol=pair.right,
                                quantity=int(abs(right_qty)),
                                metadata={"strategy": "RELATIVE_VALUE", "reason": "SPREAD_EXIT"},
                            )
                        )
                    self._pair_age[pair.key] = 0
                continue

            if corr < self.min_corr or abs(z) < self.entry_z:
                continue

            pair_allocation = self.portfolio.latest_equity * self.max_notional_pct * self.pair_risk_pct / max(self.entry_z, 1e-9)
            left_allocation = pair_allocation / 2.0
            right_allocation = pair_allocation / 2.0 * max(abs(beta), 0.5)
            left_units = max(floor(left_allocation / max(left_bar.close, 1e-9)), 0)
            right_units = max(floor(right_allocation / max(right_bar.close, 1e-9)), 0)
            if left_units <= 0 or right_units <= 0:
                continue

            self._pair_age[pair.key] = 0
            if z > self.entry_z:
                signals.append(
                    self.buy_moo(
                        timestamp=market_event.timestamp,
                        symbol=pair.left,
                        quantity=left_units,
                        metadata={"strategy": "RELATIVE_VALUE", "pair": pair.key},
                    )
                )
                signals.append(
                    self.sell_moo(
                        timestamp=market_event.timestamp,
                        symbol=pair.right,
                        quantity=right_units,
                        metadata={
                            "strategy": "RELATIVE_VALUE",
                            "pair": pair.key,
                            "short_sale": True,
                        },
                    )
                )
            elif z < -self.entry_z:
                signals.append(
                    self.sell_moo(
                        timestamp=market_event.timestamp,
                        symbol=pair.left,
                        quantity=left_units,
                        metadata={
                            "strategy": "RELATIVE_VALUE",
                            "pair": pair.key,
                            "short_sale": True,
                        },
                    )
                )
                signals.append(
                    self.buy_moo(
                        timestamp=market_event.timestamp,
                        symbol=pair.right,
                        quantity=right_units,
                        metadata={"strategy": "RELATIVE_VALUE", "pair": pair.key},
                    )
                )

        return signals


class LongVolatilityConvexityWizardStrategy(BaseStrategy):
    """
    Proxy implementation: the engine is linear-instrument based, so this trades liquid convexity proxies
    when implied-vol percentile is low and event-risk is high.
    """

    def __init__(
        self,
        symbols: list[str],
        volatility_source: VolatilityDataSource,
        atr_period: int = 14,
        entry_iv_percentile: float = 30.0,
        exit_iv_percentile: float = 65.0,
        min_event_risk_score: float = 0.7,
        stop_atr_mult: float = 2.0,
        spot_move_exit: float = 0.12,
        max_holding_days: int = 15,
        risk_pct: float = 0.01,
        max_notional_pct: float = 0.10,
    ) -> None:
        super().__init__(symbols=symbols)
        self.volatility_source = volatility_source
        self.atr_period = atr_period
        self.entry_iv_percentile = entry_iv_percentile
        self.exit_iv_percentile = exit_iv_percentile
        self.min_event_risk_score = min_event_risk_score
        self.stop_atr_mult = stop_atr_mult
        self.spot_move_exit = spot_move_exit
        self.max_holding_days = max_holding_days
        self.risk_pct = risk_pct
        self.max_notional_pct = max_notional_pct
        self._highs = {symbol: deque(maxlen=atr_period + 5) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=atr_period + 5) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=atr_period + 5) for symbol in symbols}
        self._entry_price: dict[str, float] = {}
        self._holding_days = {symbol: 0 for symbol in symbols}
        self._prev_qty = {symbol: 0.0 for symbol in symbols}

    def _sync_position_state(self, market_event: MarketEvent) -> None:
        if self.portfolio is None:
            return
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            prev = self._prev_qty[symbol]
            if qty == 0:
                self._entry_price.pop(symbol, None)
                self._holding_days[symbol] = 0
            elif prev <= 0 < qty:
                self._entry_price[symbol] = bar.close
                self._holding_days[symbol] = 0
            else:
                self._holding_days[symbol] += 1
            self._prev_qty[symbol] = qty

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._sync_position_state(market_event)
        signals: list[SignalEvent] = []
        if self.portfolio is None:
            return signals

        as_of = market_event.timestamp.date()
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            atr = _atr(highs, lows, closes, self.atr_period)
            if atr is None:
                continue
            vol = self.volatility_source.snapshot(symbol, as_of)
            if vol is None:
                continue

            qty = float(self.portfolio.position_for_symbol(symbol).quantity)
            if qty > 0:
                stop = self._entry_price.get(symbol, bar.close) - self.stop_atr_mult * atr
                realized_move = abs(bar.close / max(self._entry_price.get(symbol, bar.close), 1e-9) - 1.0)
                if (
                    vol.implied_vol_percentile >= self.exit_iv_percentile
                    or vol.event_risk_score < self.min_event_risk_score * 0.75
                    or realized_move >= self.spot_move_exit
                    or self._holding_days[symbol] >= self.max_holding_days
                    or bar.close <= stop
                ):
                    signals.append(
                        self.sell_moc(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(qty)),
                            metadata={"strategy": "CONVEXITY_PROXY", "reason": "VOL_EXPANSION_OR_MOVE"},
                        )
                    )
                continue

            if (
                vol.implied_vol_percentile > self.entry_iv_percentile
                or vol.event_risk_score < self.min_event_risk_score
            ):
                continue

            quantity = _risk_size(
                equity=self.portfolio.latest_equity,
                price=bar.close,
                risk_per_unit=max(self.stop_atr_mult * atr, 1e-9),
                risk_pct=self.risk_pct,
                max_notional_pct=self.max_notional_pct,
            )
            if quantity <= 0:
                continue

            signals.append(
                self.buy_moo(
                    timestamp=market_event.timestamp,
                    symbol=symbol,
                    quantity=quantity,
                    metadata={"strategy": "CONVEXITY_PROXY"},
                )
            )

        return signals
