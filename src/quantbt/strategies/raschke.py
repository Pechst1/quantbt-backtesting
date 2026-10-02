from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np

from quantbt.core.enums import OrderType
from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy

DAY = {"time_in_force": "DAY"}


@dataclass(slots=True)
class _Pending:
    """A buy stop working for the next bar, plus what is needed once it fills."""

    level: float
    rank: float
    swing_low: float  # lowest low of the setup bars so far (initial stop component)
    target: float = float("nan")


@dataclass(slots=True)
class _Open:
    stop: float
    target: float
    bars_held: int = 0


class _RaschkeSetupStrategy(BaseStrategy):
    """
    Shared machinery for the Street Smarts setups (Connors & Raschke, 1995), long only.

    Every bar: manage open positions (stop, optional target, optional time exit, all as
    one-bar day orders), then place one-bar buy stops for the best new setups that fit
    into free slots. Equal-weight slots of equity / max_positions. Subclasses only define
    the setup, the initial stop and the exits.

    Indicators are kept per symbol over real bars only; forward-filled (stale) bars are
    ignored, and a held symbol that turns stale is sold at the next real open.
    """

    tag = "RASCHKE"
    window = 201

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool] | None = None,
        max_positions: int = 10,
        trend_filter_days: int | None = None,
    ) -> None:
        super().__init__(symbols=symbols)
        self.is_member = is_member
        self.max_positions = max_positions
        self.trend_filter_days = trend_filter_days
        self._index = {symbol: i for i, symbol in enumerate(symbols)}
        n = len(symbols)
        self._high = np.full((self.window, n), np.nan)
        self._low = np.full((self.window, n), np.nan)
        self._close = np.full((self.window, n), np.nan)
        self._valid = np.zeros(n, dtype=bool)
        self._pending: dict[str, _Pending] = {}
        self._open: dict[str, _Open] = {}
        self.setups_seen = 0
        self.setups_placed = 0

    # -- data ---------------------------------------------------------------------------
    def _push(self, market_event: MarketEvent) -> None:
        n = len(self.symbols)
        high = np.full(n, np.nan)
        low = high.copy()
        close = high.copy()
        valid = np.zeros(n, dtype=bool)
        for symbol, bar in market_event.bars.items():
            i = self._index.get(symbol)
            if i is None or bar.is_stale:
                continue
            high[i], low[i], close[i] = bar.high, bar.low, bar.close
            valid[i] = True
        # Only real bars enter the history: shift columns that printed today.
        cols = np.flatnonzero(valid)
        for buf, row in ((self._high, high), (self._low, low), (self._close, close)):
            buf[:-1, cols] = buf[1:, cols]
            buf[-1, cols] = row[cols]
        self._valid = valid
        self._update_indicators(valid, high, low, close)

    def _update_indicators(self, valid: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> None:
        """Hook for recursive indicators (EMA, ADX)."""

    def _trend_ok(self) -> np.ndarray:
        if self.trend_filter_days is None:
            return np.ones(len(self.symbols), dtype=bool)
        hist = self._close[-self.trend_filter_days :]
        with np.errstate(invalid="ignore"):
            sma = hist.mean(axis=0)  # NaN until a full window of real bars exists
            return self._close[-1] > sma

    # -- subclass interface --------------------------------------------------------------
    def _setups(self) -> dict[int, _Pending]:
        raise NotImplementedError

    def _on_entry(self, symbol: str, i: int, pending: _Pending) -> _Open:
        raise NotImplementedError

    def _trail(self, state: _Open, i: int) -> None:
        """Update the stop at a close after the entry bar."""

    def _time_exit(self, state: _Open) -> bool:
        return False

    # -- main loop -----------------------------------------------------------------------
    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        signals: list[SignalEvent] = []

        held: dict[str, int] = {
            s: int(p.quantity) for s, p in self.portfolio.positions.items() if int(p.quantity) > 0
        }
        for symbol in list(self._open):
            if symbol not in held:
                del self._open[symbol]

        exit_value = 0.0
        for symbol, qty in held.items():
            i = self._index.get(symbol)
            if i is None:
                continue
            state = self._open.get(symbol)
            if state is None:
                pending = self._pending.get(symbol)
                if pending is None:  # should not happen; protect the position anyway
                    pending = _Pending(level=float(self._close[-1, i]), rank=0.0, swing_low=float(self._low[-1, i]))
                state = self._on_entry(symbol, i, pending)
                self._open[symbol] = state
            elif self._valid[i]:
                state.bars_held += 1
                self._trail(state, i)

            meta = {"strategy": self.tag, "is_protective": True, **DAY}
            if not self._valid[i] and not self._is_listed_today(market_event, symbol):
                signals.append(self.sell_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={**meta, "reason": "NO_DATA"}))
                exit_value += qty * float(np.nan_to_num(self._last_close(i)))
                continue
            if self._time_exit(state):
                signals.append(self.sell_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={**meta, "reason": "TIME"}))
                exit_value += qty * float(self._last_close(i))
                continue
            signals.append(
                self.signal(
                    timestamp=ts,
                    symbol=symbol,
                    side=self._sell_side(),
                    quantity=qty,
                    order_type=OrderType.STOP_LOSS,
                    stop_price=state.stop,
                    metadata={**meta, "reason": "STOP"},
                )
            )
            if np.isfinite(state.target):
                signals.append(
                    self.signal(
                        timestamp=ts,
                        symbol=symbol,
                        side=self._sell_side(),
                        quantity=qty,
                        order_type=OrderType.TAKE_PROFIT,
                        limit_price=state.target,
                        metadata={**meta, "reason": "TARGET"},
                    )
                )

        setups = self._setups()
        self.setups_seen += len(setups)
        self._pending = {}
        as_of = ts.date()
        candidates = [
            (i, p)
            for i, p in setups.items()
            if self.symbols[i] not in held and (self.is_member is None or self.is_member(self.symbols[i], as_of))
        ]
        candidates.sort(key=lambda item: -item[1].rank)
        free = self.max_positions - len(held)
        if free <= 0 or not candidates:
            return signals

        equity = self.portfolio.latest_equity
        cash = self.portfolio.cash + exit_value
        slot_value = equity / self.max_positions
        for i, pending in candidates[:free]:
            budget = min(slot_value, cash)
            # Leave room for a gap through the stop and trading costs.
            quantity = int(budget * 0.97 // pending.level)
            if quantity <= 0:
                break
            cash -= quantity * pending.level
            symbol = self.symbols[i]
            self._pending[symbol] = pending
            self.setups_placed += 1
            signals.append(
                self.signal(
                    timestamp=ts,
                    symbol=symbol,
                    side=self._buy_side(),
                    quantity=quantity,
                    order_type=OrderType.STOP_LOSS,
                    stop_price=pending.level,
                    metadata={"strategy": self.tag, "rank": pending.rank, **DAY},
                )
            )
        return signals

    @staticmethod
    def _is_listed_today(market_event: MarketEvent, symbol: str) -> bool:
        bar = market_event.bars.get(symbol)
        return bar is not None and not bar.is_stale

    def _last_close(self, i: int) -> float:
        col = self._close[:, i]
        finite = col[np.isfinite(col)]
        return float(finite[-1]) if len(finite) else 0.0

    @staticmethod
    def _buy_side():
        from quantbt.core.enums import Side

        return Side.BUY

    @staticmethod
    def _sell_side():
        from quantbt.core.enums import Side

        return Side.SELL


class TurtleSoupPlusOneStrategy(_RaschkeSetupStrategy):
    """
    Turtle Soup Plus One, buy side (Street Smarts ch. 4; Raschke credits Sperandeo's 2B).

    Day 1: a new 20-day low (low below the lowest low of the prior 20 bars), a close at or
    below that prior low, and the prior low set at least three sessions earlier.
    Day 2: buy stop at the prior 20-day low, good for the day.
    Initial stop: the lower of the day-1 and day-2 lows. After that the stop trails up to
    each prior bar's low. A position still open after ``max_hold`` bars is sold at the
    next open (Street Smarts calls these 2-6 day trades).
    Ranking: deepest close below the prior low first.
    """

    tag = "TURTLE_SOUP_PLUS_ONE"

    def __init__(self, *args, lookback: int = 20, min_age: int = 3, max_hold: int = 6, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lookback = lookback
        self.min_age = min_age
        self.max_hold = max_hold

    def _setups(self) -> dict[int, _Pending]:
        prior = self._low[-1 - self.lookback : -1]  # bars t-20 .. t-1
        complete = np.isfinite(prior).all(axis=0) & self._valid
        trend = self._trend_ok()
        out: dict[int, _Pending] = {}
        for i in np.flatnonzero(complete & trend):
            col = prior[:, i]
            level = float(col.min())
            low_t, close_t = self._low[-1, i], self._close[-1, i]
            if not (low_t < level and close_t <= level):
                continue
            # Age of the prior low in sessions before day 1 (use the most recent bar at that low).
            k = self.lookback - 1 - int(np.flatnonzero(col == level)[-1])
            age = k + 1
            if age < self.min_age:
                continue
            out[i] = _Pending(level=level, rank=(level - close_t) / level, swing_low=float(low_t))
        return out

    def _on_entry(self, symbol: str, i: int, pending: _Pending) -> _Open:
        return _Open(stop=min(pending.swing_low, float(self._low[-1, i])), target=float("nan"))

    def _trail(self, state: _Open, i: int) -> None:
        state.stop = max(state.stop, float(self._low[-1, i]))

    def _time_exit(self, state: _Open) -> bool:
        return state.bars_held >= self.max_hold


class HolyGrailStrategy(_RaschkeSetupStrategy):
    """
    Holy Grail, buy side (Street Smarts ch. 6).

    Setup bar: ADX(14) > 30, +DI > -DI, and the low touches the 20-period EMA.
    Next bar: buy stop at the setup bar's high, good for the day; re-armed at the new high
    while consecutive bars keep setting up.
    Initial stop: lowest low from the first setup bar through the entry bar (the new swing
    low), fixed. Target: highest high of the 20 bars before the first setup bar (the recent
    swing high). Ranking: highest ADX first.
    """

    tag = "HOLY_GRAIL"

    def __init__(self, *args, adx_period: int = 14, adx_min: float = 30.0, ema_period: int = 20, swing_lookback: int = 20, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adx_period = adx_period
        self.adx_min = adx_min
        self.ema_period = ema_period
        self.swing_lookback = swing_lookback
        n = len(self.symbols)
        self._ema = np.full(n, np.nan)
        self._prev_high = np.full(n, np.nan)
        self._prev_low = np.full(n, np.nan)
        self._prev_close = np.full(n, np.nan)
        self._tr = np.full(n, np.nan)
        self._pdm = np.full(n, np.nan)
        self._mdm = np.full(n, np.nan)
        self._adx = np.full(n, np.nan)
        self._count = np.zeros(n, dtype=int)
        self._dx_sum = np.zeros(n)
        self._plus_di = np.full(n, np.nan)
        self._minus_di = np.full(n, np.nan)
        self._armed: dict[str, _Pending] = {}

    def _update_indicators(self, valid: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> None:
        p = self.adx_period
        a = 2.0 / (self.ema_period + 1.0)
        for i in np.flatnonzero(valid):
            h, l, c = high[i], low[i], close[i]
            self._ema[i] = c if np.isnan(self._ema[i]) else self._ema[i] + a * (c - self._ema[i])
            if self._count[i] > 0:
                ph, pl, pc = self._prev_high[i], self._prev_low[i], self._prev_close[i]
                tr = max(h - l, abs(h - pc), abs(l - pc))
                up, down = h - ph, pl - l
                pdm = up if (up > down and up > 0) else 0.0
                mdm = down if (down > up and down > 0) else 0.0
                k = self._count[i]  # number of TR values after this one is k
                if k <= p:  # build the first Wilder sums
                    self._tr[i] = (0.0 if k == 1 else self._tr[i]) + tr
                    self._pdm[i] = (0.0 if k == 1 else self._pdm[i]) + pdm
                    self._mdm[i] = (0.0 if k == 1 else self._mdm[i]) + mdm
                else:
                    self._tr[i] += tr - self._tr[i] / p
                    self._pdm[i] += pdm - self._pdm[i] / p
                    self._mdm[i] += mdm - self._mdm[i] / p
                if k >= p and self._tr[i] > 0:
                    pdi = 100.0 * self._pdm[i] / self._tr[i]
                    mdi = 100.0 * self._mdm[i] / self._tr[i]
                    self._plus_di[i], self._minus_di[i] = pdi, mdi
                    dx = 100.0 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
                    n_dx = k - p + 1
                    if n_dx < p:
                        self._dx_sum[i] += dx
                    elif n_dx == p:
                        self._adx[i] = (self._dx_sum[i] + dx) / p
                    else:
                        self._adx[i] = (self._adx[i] * (p - 1) + dx) / p
            self._count[i] += 1
            self._prev_high[i], self._prev_low[i], self._prev_close[i] = h, l, c

    def _setups(self) -> dict[int, _Pending]:
        ready = self._valid & (self._count >= 3 * self.adx_period + self.ema_period) & self._trend_ok()
        with np.errstate(invalid="ignore"):
            is_setup = (
                ready
                & (self._adx > self.adx_min)
                & (self._plus_di > self._minus_di)
                & (self._low[-1] <= self._ema)
            )
        armed: dict[str, _Pending] = {}
        out: dict[int, _Pending] = {}
        for i in np.flatnonzero(is_setup):
            symbol = self.symbols[i]
            high_t, low_t = float(self._high[-1, i]), float(self._low[-1, i])
            previous = self._armed.get(symbol)
            if previous is not None:  # consecutive setup bar: keep the first swing high
                target = previous.target
                swing_low = min(previous.swing_low, low_t)
            else:
                window = self._high[-1 - self.swing_lookback : -1, i]
                if not np.isfinite(window).all():
                    continue
                target = float(window.max())
                swing_low = low_t
            pending = _Pending(level=high_t, rank=float(self._adx[i]), swing_low=swing_low, target=target)
            armed[symbol] = pending
            if target > high_t:
                out[i] = pending
        self._armed = armed
        return out

    def _on_entry(self, symbol: str, i: int, pending: _Pending) -> _Open:
        return _Open(stop=min(pending.swing_low, float(self._low[-1, i])), target=pending.target)
