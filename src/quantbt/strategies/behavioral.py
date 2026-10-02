from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from quantbt.altdata.base import (
    CorporateActionsDataSource,
    EarningsDataSource,
    FactorDataSource,
    FundamentalsDataSource,
)
from quantbt.altdata.models import EarningsAnnouncement
from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


def _rsi(values: np.ndarray, period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    deltas = np.diff(values)
    recent = deltas[-period:]
    gains = np.where(recent > 0, recent, 0.0)
    losses = np.where(recent < 0, -recent, 0.0)
    avg_gain = float(gains.mean())
    avg_loss = float(losses.mean())
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class PEADInconsistencyAvoidanceStrategy(BaseStrategy):
    """
    Strategy 1: Inconsistency-Avoidance Arbitrage (PEAD)

    Signal:
        SUE = (EPS_actual - EPS_estimate) / sigma(estimate)
    Entry:
        If SUE > threshold on announcement day, queue LONG for next open (MOO).
    Exit:
        Day + hold_days, or day before next earnings announcement, whichever is earlier.
    """

    def __init__(
        self,
        symbols: list[str],
        earnings_source: EarningsDataSource,
        sue_threshold: float = 2.0,
        hold_days: int = 60,
    ) -> None:
        super().__init__(symbols=symbols)
        self.earnings_source = earnings_source
        self.sue_threshold = sue_threshold
        self.hold_days = hold_days
        self._planned_exits: dict[str, date] = {}

    def _entry_signals_for_today(self, market_event: MarketEvent) -> list[SignalEvent]:
        today = market_event.timestamp.date()
        if self.portfolio is None:
            return []

        # Generate MOO entries on announcement day; they execute at next session open (t+1).
        by_symbol: dict[str, tuple[EarningsAnnouncement, date]] = {}
        for event in self.earnings_source.announcements_on(today):
            symbol = event.symbol.upper()
            if symbol not in self.symbols:
                continue
            if event.estimate_std <= 0:
                continue
            if event.sue <= self.sue_threshold:
                continue

            next_announcement = event.next_announcement_ts
            if next_announcement is None:
                next_announcement = self.earnings_source.next_announcement_after(symbol, today)
            planned_exit = today + timedelta(days=self.hold_days)
            if next_announcement is not None:
                planned_exit = min(planned_exit, next_announcement.date() - timedelta(days=1))

            current = by_symbol.get(symbol)
            if current is None or event.sue > current[0].sue:
                by_symbol[symbol] = (event, planned_exit)

        if not by_symbol:
            return []

        bars = market_event.bars
        allocation = max(self.portfolio.latest_equity / max(len(by_symbol), 1), 0.0)
        signals: list[SignalEvent] = []
        for symbol, (event, planned_exit) in by_symbol.items():
            bar = bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity > 0:
                continue
            quantity = max(int(allocation / bar.close), 1)
            self._planned_exits[symbol] = planned_exit
            signals.append(
                self.buy_moo(
                    timestamp=market_event.timestamp,
                    symbol=symbol,
                    quantity=quantity,
                    metadata={
                        "strategy": "PEAD",
                        "sue": event.sue,
                        "announcement_ts": event.announcement_ts.isoformat(),
                    },
                )
            )
        return signals

    def _exit_signals_for_today(self, market_event: MarketEvent) -> list[SignalEvent]:
        today = market_event.timestamp.date()
        if self.portfolio is None:
            return []
        signals: list[SignalEvent] = []
        for symbol, exit_date in list(self._planned_exits.items()):
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity <= 0:
                self._planned_exits.pop(symbol, None)
                continue
            if today >= exit_date:
                signals.append(
                    self.sell_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=int(abs(position.quantity)),
                        metadata={"strategy": "PEAD", "reason": "HOLD_WINDOW_END"},
                    )
                )
                self._planned_exits.pop(symbol, None)
        return signals

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        # Process exits before entries. MOO entries generated today fill on the next bar,
        # so running exits first avoids clearing freshly planned exits before the fill occurs.
        signals.extend(self._exit_signals_for_today(market_event))
        signals.extend(self._entry_signals_for_today(market_event))
        return signals


class AntiLotteryShortStrategy(BaseStrategy):
    """
    Strategy 2: Anti-Lottery Short

    Signal:
        Rank by idiosyncratic volatility (residual sigma) from a 30-day regression:
        (R_i - R_f) = alpha + beta1*(MKT-RF) + beta2*SMB + beta3*HML + epsilon
    Universe filter:
        Price >= min_price and PiT net_income_ttm < 0.
    Entry:
        Rebalance monthly into top tail of idiosyncratic volatility (short).
    Exit:
        Monthly rebalance out, positive fundamentals, or 20% stop-loss.
    """

    def __init__(
        self,
        symbols: list[str],
        fundamentals_source: FundamentalsDataSource,
        factor_source: FactorDataSource,
        rebalance_top_pct: float = 0.05,
        lookback_days: int = 30,
        min_price: float = 5.0,
        stop_loss_pct: float = 0.20,
    ) -> None:
        super().__init__(symbols=symbols)
        self.fundamentals_source = fundamentals_source
        self.factor_source = factor_source
        self.rebalance_top_pct = rebalance_top_pct
        self.lookback_days = lookback_days
        self.min_price = min_price
        self.stop_loss_pct = stop_loss_pct
        self._price_history: dict[str, deque[tuple[datetime, float]]] = {
            symbol: deque(maxlen=lookback_days + 20) for symbol in symbols
        }
        self._short_entry_price: dict[str, float] = {}
        self._last_observed_date: date | None = None
        self._last_rebalance_month: tuple[int, int] | None = None

    def _is_eligible(self, symbol: str, as_of: date) -> bool:
        snapshot = self.fundamentals_source.snapshot(symbol, as_of)
        if snapshot is None:
            return False
        return snapshot.net_income_ttm < 0.0

    def _idiosyncratic_vol(self, symbol: str, as_of: date) -> float | None:
        history = self._price_history[symbol]
        if len(history) < self.lookback_days + 1:
            return None
        index = pd.DatetimeIndex([ts for ts, _ in history])
        closes = pd.Series([price for _, price in history], index=index, dtype=float)
        returns = closes.pct_change().dropna().tail(self.lookback_days)
        if len(returns) < self.lookback_days:
            return None

        factors = self.factor_source.factor_window(returns.index)
        aligned = pd.concat([returns.rename("ret"), factors], axis=1).dropna()
        if len(aligned) < max(10, self.lookback_days // 2):
            return None

        y = aligned["ret"].to_numpy(dtype=float) - aligned["rf"].to_numpy(dtype=float)
        x = np.column_stack(
            [
                np.ones(len(aligned), dtype=float),
                aligned["market_excess"].to_numpy(dtype=float),
                aligned["smb"].to_numpy(dtype=float),
                aligned["hml"].to_numpy(dtype=float),
            ]
        )
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        residual = y - x @ beta
        return float(np.std(residual, ddof=0))

    def _monthly_rebalance(self, market_event: MarketEvent) -> list[SignalEvent]:
        if self.portfolio is None:
            return []
        as_of = market_event.timestamp.date()
        scores: list[tuple[str, float]] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            if bar.close < self.min_price:
                continue
            if not self._is_eligible(symbol, as_of):
                continue
            vol = self._idiosyncratic_vol(symbol, as_of)
            if vol is None:
                continue
            scores.append((symbol, vol))

        if not scores:
            return []

        scores.sort(key=lambda item: item[1], reverse=True)
        top_n = max(int(np.ceil(len(scores) * self.rebalance_top_pct)), 1)
        target_shorts = {symbol for symbol, _ in scores[:top_n]}
        allocation = max(self.portfolio.latest_equity / max(len(target_shorts), 1), 0.0)

        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            position = self.portfolio.position_for_symbol(symbol)
            bar = market_event.bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            fundamentals_ok = self._is_eligible(symbol, as_of)
            if position.quantity < 0:
                should_cover = (symbol not in target_shorts) or (not fundamentals_ok)
                if should_cover:
                    signals.append(
                        self.buy_moo(
                            timestamp=market_event.timestamp,
                            symbol=symbol,
                            quantity=int(abs(position.quantity)),
                            metadata={"strategy": "ANTI_LOTTERY", "reason": "MONTHLY_REBALANCE"},
                        )
                    )
                    self._short_entry_price.pop(symbol, None)
                continue
            if symbol in target_shorts and position.quantity >= 0:
                quantity = max(int(allocation / bar.close), 1)
                signals.append(
                    self.sell_moo(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=quantity,
                        metadata={
                            "strategy": "ANTI_LOTTERY",
                            "short_sale": True,
                            "reason": "IDIO_VOL_TOP_BUCKET",
                        },
                    )
                )
                self._short_entry_price[symbol] = bar.close
        return signals

    def _stop_loss_signals(self, market_event: MarketEvent) -> list[SignalEvent]:
        if self.portfolio is None:
            return []
        signals: list[SignalEvent] = []
        as_of = market_event.timestamp.date()
        for symbol in self.symbols:
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity >= 0:
                self._short_entry_price.pop(symbol, None)
                continue
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            entry = self._short_entry_price.get(symbol, position.avg_price)
            if entry <= 0:
                continue
            hit_stop = bar.close >= entry * (1.0 + self.stop_loss_pct)
            fundamentals_flipped = not self._is_eligible(symbol, as_of)
            if hit_stop or fundamentals_flipped:
                signals.append(
                    self.buy_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=int(abs(position.quantity)),
                        metadata={
                            "strategy": "ANTI_LOTTERY",
                            "reason": "STOP_LOSS" if hit_stop else "FUNDAMENTALS_FLIP",
                        },
                    )
                )
                self._short_entry_price.pop(symbol, None)
        return signals

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        today = market_event.timestamp.date()
        signals: list[SignalEvent] = []

        # Rebalance at first bar of a new month using the trailing history.
        current_month = (today.year, today.month)
        if self._last_rebalance_month is None:
            self._last_rebalance_month = current_month
        elif current_month != self._last_rebalance_month:
            signals.extend(self._monthly_rebalance(market_event))
            self._last_rebalance_month = current_month

        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._price_history[symbol].append((market_event.timestamp, bar.close))

        signals.extend(self._stop_loss_signals(market_event))
        self._last_observed_date = today
        return signals


class CapitulationBuyStrategy(BaseStrategy):
    """
    Strategy 3: Capitulation Buy

    Entry signal (all required):
    1) 3-day return <= -15%
    2) today's volume > 3 * SMA(volume, 30)
    3) RSI(14) < 20
    4) no structural corporate action on the day
    Exit:
    - RSI(14) > 50, or max_holding_days reached.
    """

    STRUCTURAL_ACTIONS = {"DIVIDEND", "SPLIT", "SPINOFF", "MERGER", "DELISTING", "SPECIAL_DIVIDEND"}

    def __init__(
        self,
        symbols: list[str],
        corporate_actions_source: CorporateActionsDataSource,
        drop_threshold: float = -0.15,
        volume_multiplier: float = 3.0,
        rsi_entry: float = 20.0,
        rsi_exit: float = 50.0,
        max_holding_days: int = 10,
    ) -> None:
        super().__init__(symbols=symbols)
        self.corporate_actions_source = corporate_actions_source
        self.drop_threshold = drop_threshold
        self.volume_multiplier = volume_multiplier
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.max_holding_days = max_holding_days
        self._close_history = {symbol: deque(maxlen=40) for symbol in symbols}
        self._volume_history = {symbol: deque(maxlen=40) for symbol in symbols}
        self._entry_dates: dict[str, date] = {}

    def _entry_candidates(self, market_event: MarketEvent) -> list[str]:
        today = market_event.timestamp.date()
        out: list[str] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            closes = self._close_history[symbol]
            volumes = self._volume_history[symbol]
            if len(closes) < 31 or len(volumes) < 31:
                continue

            arr_close = np.fromiter(closes, dtype=float)
            arr_volume = np.fromiter(volumes, dtype=float)
            three_day_return = arr_close[-1] / arr_close[-4] - 1.0
            avg_volume = float(np.mean(arr_volume[-31:-1]))
            rsi_value = _rsi(arr_close, period=14)
            structural = self.corporate_actions_source.has_action(
                symbol=symbol,
                as_of=today,
                action_types=self.STRUCTURAL_ACTIONS,
            )
            if (
                three_day_return <= self.drop_threshold
                and bar.volume > self.volume_multiplier * max(avg_volume, 1.0)
                and rsi_value < self.rsi_entry
                and not structural
            ):
                out.append(symbol)
        return out

    def _exit_signals(self, market_event: MarketEvent) -> list[SignalEvent]:
        if self.portfolio is None:
            return []
        today = market_event.timestamp.date()
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            position = self.portfolio.position_for_symbol(symbol)
            if position.quantity <= 0:
                self._entry_dates.pop(symbol, None)
                continue
            closes = self._close_history[symbol]
            if len(closes) < 15:
                continue
            rsi_value = _rsi(np.fromiter(closes, dtype=float), period=14)
            entry_day = self._entry_dates.get(symbol, today)
            holding_days = (today - entry_day).days
            if rsi_value > self.rsi_exit or holding_days >= self.max_holding_days:
                signals.append(
                    self.sell_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=int(abs(position.quantity)),
                        metadata={"strategy": "CAPITULATION_BUY", "reason": "PANIC_NORMALIZED"},
                    )
                )
                self._entry_dates.pop(symbol, None)
        return signals

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._close_history[symbol].append(bar.close)
            self._volume_history[symbol].append(bar.volume)

        signals: list[SignalEvent] = []
        signals.extend(self._exit_signals(market_event))

        if self.portfolio is None:
            return signals
        candidates = self._entry_candidates(market_event)
        if candidates:
            allocation = max(self.portfolio.latest_equity / len(candidates), 0.0)
            for symbol in candidates:
                position = self.portfolio.position_for_symbol(symbol)
                if position.quantity > 0:
                    continue
                bar = market_event.bars.get(symbol)
                if bar is None or bar.close <= 0:
                    continue
                quantity = max(int(allocation / bar.close), 1)
                signals.append(
                    self.buy_moc(
                        timestamp=market_event.timestamp,
                        symbol=symbol,
                        quantity=quantity,
                        metadata={"strategy": "CAPITULATION_BUY"},
                    )
                )
                self._entry_dates[symbol] = market_event.timestamp.date()
        return signals
