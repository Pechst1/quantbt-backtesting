"""
Price-only translations of three Stock Market Wizards methods (Schwager, 2001).

Rules and parameters are fixed in reports/stock_market_wizards/PREREGISTRATION.md:
  - OkumusDeepValueStrategy (H1): buy index members down 60%+ from their 1-year high.
  - CookBreadthTimingStrategy (H2): time SPY with a cumulative-breadth proxy for Cook's
    Cumulative Tick.
  - ShortTermReversalStrategy (H3): hold last week's (month's) worst large-cap performers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date

import numpy as np

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


class _PanelStrategy(BaseStrategy):
    """Shared plumbing: a rolling close buffer (NaN for stale bars) and delisting handling."""

    tag = "WIZARDS"

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        window: int,
        market_symbol: str = "SPY",
        dead_after_bars: int = 5,
    ) -> None:
        super().__init__(symbols=symbols)
        self.is_member = is_member
        self.market_symbol = market_symbol
        self.dead_after_bars = dead_after_bars
        self._index = {symbol: i for i, symbol in enumerate(symbols)}
        self._close = np.full((window, len(symbols)), np.nan)
        self._stale_run = np.zeros(len(symbols), dtype=int)
        self._dead: set[str] = set()
        self._pending_exit: set[str] = set()
        self.bar_count = 0

    def _push(self, market_event: MarketEvent) -> np.ndarray:
        close = np.full(len(self.symbols), np.nan)
        stale = np.ones(len(self.symbols), dtype=bool)
        for symbol, bar in market_event.bars.items():
            i = self._index.get(symbol)
            if i is None:
                continue
            stale[i] = bar.is_stale
            if not bar.is_stale:
                close[i] = bar.close
        self._close[:-1] = self._close[1:]
        self._close[-1] = close
        self._stale_run = np.where(stale, self._stale_run + 1, 0)
        self.bar_count += 1
        return stale

    def _members(self, as_of: date, stale: np.ndarray) -> np.ndarray:
        return np.array(
            [
                (not stale[i]) and s != self.market_symbol and self.is_member(s, as_of)
                for i, s in enumerate(self.symbols)
            ]
        )

    def _sell(self, ts, symbol: str, qty: int, reason: str) -> SignalEvent:
        self._pending_exit.add(symbol)
        return self.sell_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={"strategy": self.tag, "reason": reason})

    def _buy(self, ts, symbol: str, qty: int, **meta) -> SignalEvent:
        return self.buy_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={"strategy": self.tag, **meta})

    def _live_holdings(self, ts, signals: list[SignalEvent]) -> dict[str, int]:
        """Long holdings that are tradable and not already being sold; flags dead names."""
        holdings = {s: int(p.quantity) for s, p in self.portfolio.positions.items() if int(p.quantity) > 0}
        self._pending_exit &= set(holdings)
        live: dict[str, int] = {}
        for symbol, qty in holdings.items():
            i = self._index.get(symbol)
            if i is None or symbol in self._dead:
                continue
            if self._stale_run[i] >= self.dead_after_bars:
                # Delisted or acquired: the last price stays in equity, one exit stays pending.
                self._dead.add(symbol)
                if symbol not in self._pending_exit:
                    signals.append(self._sell(ts, symbol, qty, "NO_DATA"))
                continue
            if symbol in self._pending_exit:
                continue
            live[symbol] = qty
        return live

    @staticmethod
    def _dedupe(order: Iterable[int], price: np.ndarray, score: np.ndarray) -> list[int]:
        # The membership history sometimes lists one company under two tickers at once
        # (KORS/CPRI, PX/LIN, CCE/CCEP); identical price and score means the same stock.
        seen: set[tuple[float, float]] = set()
        out: list[int] = []
        for i in order:
            key = (round(float(price[i]), 4), round(float(score[i]), 6))
            if key in seen:
                continue
            seen.add(key)
            out.append(int(i))
        return out


class OkumusDeepValueStrategy(_PanelStrategy):
    """
    H1. Buy index members whose close is at most (1 - min_drop) of their 252-day high close.
    At most ``max_positions``, each funded with equity / max_positions; deepest decline first.
    Sell at the next open after a close >= target_mult x entry, or after ``time_stop_bars``.
    No loss stop, no forced sale on leaving the index.
    """

    tag = "OKUMUS"

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        min_drop: float = 0.60,
        lookback: int = 252,
        max_positions: int = 10,
        target_mult: float = 1.5,
        time_stop_bars: int = 504,
        market_symbol: str = "SPY",
    ) -> None:
        super().__init__(symbols, is_member, window=lookback, market_symbol=market_symbol)
        self.min_drop = min_drop
        self.lookback = lookback
        self.max_positions = max_positions
        self.target_mult = target_mult
        self.time_stop_bars = time_stop_bars
        self._entry_bar: dict[str, int] = {}
        self.candidates_seen = 0

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        stale = self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        price = self._close[-1]
        signals: list[SignalEvent] = []
        live = self._live_holdings(ts, signals)

        exit_proceeds = 0.0
        sold_today = 0
        for symbol, qty in list(live.items()):
            i = self._index[symbol]
            if stale[i]:
                continue
            entry = self.portfolio.positions[symbol].avg_price
            held = self.bar_count - self._entry_bar.get(symbol, self.bar_count)
            reason = None
            if price[i] >= entry * self.target_mult:
                reason = "TARGET"
            elif held >= self.time_stop_bars:
                reason = "TIME_STOP"
            if reason:
                signals.append(self._sell(ts, symbol, qty, reason))
                exit_proceeds += qty * price[i]
                sold_today += 1
                del live[symbol]

        occupied = sum(1 for p in self.portfolio.positions.values() if int(p.quantity) > 0) - sold_today
        free_slots = self.max_positions - occupied
        if free_slots <= 0 or self.bar_count < self.lookback:
            return signals

        with np.errstate(invalid="ignore"):
            high = np.nanmax(self._close, axis=0)
            enough = np.sum(np.isfinite(self._close), axis=0) >= self.lookback
            ratio = price / high
        eligible = self._members(ts.date(), stale) & enough & np.isfinite(ratio) & (ratio <= 1.0 - self.min_drop)
        idx = np.flatnonzero(eligible)
        if len(idx) == 0:
            return signals
        self.candidates_seen += len(idx)
        held_now = {s for s, p in self.portfolio.positions.items() if int(p.quantity) != 0} | self._pending_exit
        order = self._dedupe(idx[np.argsort(ratio[idx])], price, ratio)

        cash = self.portfolio.cash + exit_proceeds
        slot_value = self.portfolio.latest_equity / self.max_positions
        for i in order:
            if free_slots <= 0:
                break
            symbol = self.symbols[i]
            if symbol in held_now:
                continue
            quantity = int(min(slot_value, cash) * 0.98 // price[i])
            if quantity <= 0:
                break
            cash -= quantity * price[i]
            free_slots -= 1
            self._entry_bar[symbol] = self.bar_count
            signals.append(self._buy(ts, symbol, quantity, drop=float(1.0 - ratio[i])))
        return signals


class CookBreadthTimingStrategy(_PanelStrategy):
    """
    H2. Daily breadth b = (advancers - decliners) / n over current index members; days with
    |b| <= quiet_band count as zero; C = sum over ``sum_window`` days. Percentiles of C use
    only its own past (expanding, at least ``min_history`` values).

    mode "cash":      flat normally; 100% SPY while a buy signal is on.
    mode "levered":   100% SPY normally; ``signal_exposure`` x SPY while a buy signal is on
                      (only while SPY closes above its ``trend_sma``-day SMA, if set).
    mode "sell_tops": 100% SPY normally; cash while a sell signal is on.
    A buy signal starts when C < 5th percentile and ends on the first close with C >= median.
    A sell signal starts when C > 95th percentile and ends on the first close with C <= median.
    """

    tag = "COOK"

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        mode: str = "cash",
        quiet_band: float = 400 / 3000,
        sum_window: int = 21,
        min_history: int = 504,
        low_pct: float = 5.0,
        high_pct: float = 95.0,
        signal_exposure: float = 2.0,
        trend_sma: int | None = None,
        market_symbol: str = "SPY",
    ) -> None:
        super().__init__(symbols, is_member, window=2, market_symbol=market_symbol)
        self.signal_exposure = signal_exposure
        self.trend_sma = trend_sma
        self._market_closes: list[float] = []
        self.days_levered = 0
        if mode not in ("cash", "levered", "sell_tops"):
            raise ValueError(f"Unknown mode {mode!r}")
        self.mode = mode
        self.quiet_band = quiet_band
        self.sum_window = sum_window
        self.min_history = min_history
        self.low_pct = low_pct
        self.high_pct = high_pct
        self._x: list[float] = []
        self._c: list[float] = []
        self.state = "neutral"  # "buy", "sell" or "neutral"
        self.buy_signals = 0
        self.sell_signals = 0
        self.days_in_buy = 0
        self.days_in_sell = 0
        self.breadth_log: list[tuple[object, float, float, str]] = []
        self._last_exposure: float | None = None

    def _target_exposure(self) -> float:
        if self.mode == "cash":
            return 1.0 if self.state == "buy" else 0.0
        if self.mode == "levered":
            if self.state != "buy":
                return 1.0
            if self.trend_sma is not None:
                window = self._market_closes[-self.trend_sma :]
                if len(window) < self.trend_sma or window[-1] <= sum(window) / len(window):
                    return 1.0
            return self.signal_exposure
        return 0.0 if self.state == "sell" else 1.0

    def _update_state(self, c: float, history: np.ndarray) -> None:
        low, mid, high = np.percentile(history, [self.low_pct, 50.0, self.high_pct])
        if self.state == "buy" and c >= mid:
            self.state = "neutral"
        elif self.state == "sell" and c <= mid:
            self.state = "neutral"
        if self.state == "neutral":
            if c < low:
                self.state = "buy"
                self.buy_signals += 1
            elif c > high and self.mode == "sell_tops":
                self.state = "sell"
                self.sell_signals += 1
        self.days_in_buy += self.state == "buy"
        self.days_in_sell += self.state == "sell"

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        stale = self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        prev, now = self._close[0], self._close[1]
        members = self._members(ts.date(), stale) & np.isfinite(prev) & np.isfinite(now)
        n = int(members.sum())
        signals: list[SignalEvent] = []
        if n < 50:
            return signals
        diff = now[members] - prev[members]
        b = (int((diff > 0).sum()) - int((diff < 0).sum())) / n
        self._x.append(b if abs(b) > self.quiet_band else 0.0)
        if len(self._x) >= self.sum_window:
            c = float(sum(self._x[-self.sum_window :]))
            history = np.asarray(self._c)
            self._c.append(c)
            if len(history) >= self.min_history:
                self._update_state(c, history)
                self.breadth_log.append((ts, b, c, self.state))

        m = self._index[self.market_symbol]
        if stale[m]:
            return signals
        price = now[m]
        self._market_closes.append(float(price))
        exposure = self._target_exposure()
        self.days_levered += exposure > 1.0
        pos = self.portfolio.positions.get(self.market_symbol)
        qty_now = int(pos.quantity) if pos else 0
        # Trade only when the target exposure changes, not on drift from SPY's own moves.
        if exposure == self._last_exposure:
            return signals
        self._last_exposure = exposure
        target_qty = int(self.portfolio.latest_equity * exposure * 0.98 // price)
        delta = target_qty - qty_now
        if delta > 0:
            signals.append(self._buy(ts, self.market_symbol, delta, state=self.state))
        elif delta < 0:
            signals.append(self.sell_moo(timestamp=ts, symbol=self.market_symbol, quantity=-delta, metadata={"strategy": self.tag, "reason": self.state.upper()}))
        return signals


class ShortTermReversalStrategy(_PanelStrategy):
    """
    H3. On each signal date (close), rank current members by their ``lookback``-day return and
    hold the bottom ``fraction`` (worst performers) equally weighted until the next signal.
    Names still in the bottom group are kept; the rest are sold; new names get equity / N.
    """

    tag = "REVERSAL"

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        signal_dates: set[date],
        lookback: int = 5,
        fraction: float = 0.10,
        market_symbol: str = "SPY",
    ) -> None:
        super().__init__(symbols, is_member, window=lookback + 1, market_symbol=market_symbol)
        self.signal_dates = signal_dates
        self.lookback = lookback
        self.fraction = fraction
        self.rebalances = 0
        self.names_traded = 0

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        stale = self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        signals: list[SignalEvent] = []
        live = self._live_holdings(ts, signals)
        if ts.date() not in self.signal_dates or self.bar_count <= self.lookback:
            return signals
        self.rebalances += 1
        price = self._close[-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = price / self._close[0] - 1.0
        eligible = self._members(ts.date(), stale) & np.isfinite(ret)
        idx = np.flatnonzero(eligible)
        n_target = int(round(len(idx) * self.fraction))
        if n_target <= 0:
            return signals
        losers = self._dedupe(idx[np.argsort(ret[idx])], price, ret)[:n_target]
        target = {self.symbols[i] for i in losers}

        exit_proceeds = 0.0
        for symbol, qty in list(live.items()):
            if symbol not in target:
                signals.append(self._sell(ts, symbol, qty, "ROTATED_OUT"))
                exit_proceeds += qty * price[self._index[symbol]] if np.isfinite(price[self._index[symbol]]) else 0.0
                del live[symbol]

        cash = self.portfolio.cash + exit_proceeds
        slot_value = self.portfolio.latest_equity / n_target
        held_now = {s for s, p in self.portfolio.positions.items() if int(p.quantity) != 0} | self._pending_exit
        for i in losers:
            symbol = self.symbols[i]
            if symbol in live or symbol in held_now:
                continue
            quantity = int(min(slot_value, cash) * 0.98 // price[i])
            if quantity <= 0:
                break
            cash -= quantity * price[i]
            self.names_traded += 1
            signals.append(self._buy(ts, symbol, quantity, ret=float(ret[i])))
        return signals
