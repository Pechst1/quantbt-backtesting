from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy

DAY = {"time_in_force": "DAY"}


@dataclass(slots=True)
class _Pending:
    level: float  # planned entry (buy stop level, or the signal close for an EP)
    stop: float  # initial protective stop
    rank: float


@dataclass(slots=True)
class _Open:
    entry: float
    stop: float
    bars_held: int = 0
    partial_done: bool = False


class KullamaggieStrategy(BaseStrategy):
    """
    Kristjan Kullamägi's two long setups (Market Wizards: The Next Generation, 2026), long only.

    setup="breakout": a top-2% 1/3/6-month performer, above its 10/20/50-day SMAs after a 30%+
    move, consolidating under a pivot set 3+ bars ago with a 3-bar low within 1.2 ADR. Buy stop
    at the pivot for the next bar only; initial stop the 3-bar low (raised to the entry-bar low).
    setup="ep": a 10%+ opening gap on 3x average volume that closed at or above its open, with
    the gap-day range within 1.5 ADR. Buy at the next open; initial stop the gap-day low.

    Both: risk ``risk_per_trade`` of equity per trade, positions capped at ``max_weight``;
    sell a third at the open after the third bar following entry and move the stop to the entry
    price; sell the rest at the open after the first close below the ``trail_sma``-day SMA.
    Breakouts are only taken while the market symbol's 10-day EMA is above its 20-day EMA.

    See reports/next_gen_market_wizards/PREREGISTRATION.md for the committed rules.
    """

    tag = "KULLAMAGGIE"
    window = 130

    def __init__(
        self,
        symbols: list[str],
        setup: str = "breakout",
        is_member: Callable[[str, date], bool] | None = None,
        tradable: set[str] | None = None,
        min_price: float = 0.0,
        min_dollar_volume: float = 0.0,
        min_adr: float | None = 0.04,
        trail_sma: int = 10,
        market_symbol: str | None = "QQQ",
        risk_per_trade: float = 0.01,
        max_weight: float = 0.25,
        top_fraction: float = 0.02,
        min_prior_move: float = 0.30,
        pivot_bars: int = 10,
        pivot_min_age: int = 3,
        tight_bars: int = 3,
        max_tight_adr: float = 1.2,
        gap_min: float = 0.10,
        volume_mult: float = 3.0,
        ep_max_risk_adr: float = 1.5,
        partial_after: int = 3,
        min_pool: int = 20,
    ) -> None:
        super().__init__(symbols=symbols)
        if setup not in ("breakout", "ep"):
            raise ValueError("setup must be 'breakout' or 'ep'")
        self.setup = setup
        self.is_member = is_member
        self.min_price = min_price
        self.min_dollar_volume = min_dollar_volume
        self.min_adr = min_adr
        self.trail_sma = trail_sma
        self.market_symbol = market_symbol
        self.risk_per_trade = risk_per_trade
        self.max_weight = max_weight
        self.top_fraction = top_fraction
        self.min_prior_move = min_prior_move
        self.pivot_bars = pivot_bars
        self.pivot_min_age = pivot_min_age
        self.tight_bars = tight_bars
        self.max_tight_adr = max_tight_adr
        self.gap_min = gap_min
        self.volume_mult = volume_mult
        self.ep_max_risk_adr = ep_max_risk_adr
        self.partial_after = partial_after
        self.min_pool = min_pool

        self._index = {symbol: i for i, symbol in enumerate(symbols)}
        n = len(symbols)
        tradable = set(symbols) if tradable is None else tradable
        self._tradable = np.array([s in tradable and s != market_symbol for s in symbols])
        self._open_px = np.full((self.window, n), np.nan)
        self._high = np.full((self.window, n), np.nan)
        self._low = np.full((self.window, n), np.nan)
        self._close = np.full((self.window, n), np.nan)
        self._volume = np.full((self.window, n), np.nan)
        self._valid = np.zeros(n, dtype=bool)
        self._mkt_fast = float("nan")
        self._mkt_slow = float("nan")
        self._mkt_count = 0
        self._pending: dict[str, _Pending] = {}
        self._positions: dict[str, _Open] = {}
        self.setups_seen = 0
        self.setups_placed = 0

    # -- data ---------------------------------------------------------------------------
    def _push(self, market_event: MarketEvent) -> None:
        n = len(self.symbols)
        rows = {k: np.full(n, np.nan) for k in ("open", "high", "low", "close", "volume")}
        valid = np.zeros(n, dtype=bool)
        for symbol, bar in market_event.bars.items():
            i = self._index.get(symbol)
            if i is None or bar.is_stale:
                continue
            rows["open"][i], rows["high"][i], rows["low"][i] = bar.open, bar.high, bar.low
            rows["close"][i], rows["volume"][i] = bar.close, bar.volume
            valid[i] = True
        cols = np.flatnonzero(valid)
        for buf, key in (
            (self._open_px, "open"),
            (self._high, "high"),
            (self._low, "low"),
            (self._close, "close"),
            (self._volume, "volume"),
        ):
            buf[:-1, cols] = buf[1:, cols]
            buf[-1, cols] = rows[key][cols]
        self._valid = valid
        m = self._index.get(self.market_symbol) if self.market_symbol else None
        if m is not None and valid[m]:
            c = rows["close"][m]
            if self._mkt_count == 0:
                self._mkt_fast = self._mkt_slow = c
            else:
                self._mkt_fast += (2.0 / 11.0) * (c - self._mkt_fast)
                self._mkt_slow += (2.0 / 21.0) * (c - self._mkt_slow)
            self._mkt_count += 1

    def _market_ok(self) -> bool:
        if self.market_symbol is None:
            return True
        return self._mkt_count >= 40 and self._mkt_fast > self._mkt_slow

    def _adr(self, end: int = 0) -> np.ndarray:
        """ADR(20) as a fraction, over the 20 real bars ending ``end`` bars before the latest."""
        stop = self.window - end
        with np.errstate(invalid="ignore", divide="ignore"):
            return (self._high[stop - 20 : stop] / self._low[stop - 20 : stop] - 1.0).mean(axis=0)

    def _sma(self, n: int) -> np.ndarray:
        return self._close[-n:].mean(axis=0)

    def _eligible(self, as_of: date) -> np.ndarray:
        ok = self._valid & self._tradable
        if self.min_price > 0 or self.min_dollar_volume > 0:
            with np.errstate(invalid="ignore"):
                dollar_volume = (self._close[-20:] * self._volume[-20:]).mean(axis=0)
                ok &= (self._close[-1] >= self.min_price) & (dollar_volume >= self.min_dollar_volume)
        if self.is_member is not None:
            for i in np.flatnonzero(ok):
                if not self.is_member(self.symbols[i], as_of):
                    ok[i] = False
        return ok

    # -- setups -------------------------------------------------------------------------
    def _breakout_setups(self, eligible: np.ndarray) -> dict[int, _Pending]:
        if not self._market_ok():
            return {}
        close = self._close[-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            leader = np.zeros(len(self.symbols), dtype=bool)
            for lb in (21, 63, 126):
                ret = close / self._close[-1 - lb] - 1.0
                pool = eligible & np.isfinite(ret)
                if pool.sum() < self.min_pool:
                    continue
                cutoff = np.quantile(ret[pool], 1.0 - self.top_fraction)
                leader |= pool & (ret >= cutoff)
            adr = self._adr()
            cand = leader & np.isfinite(adr)
            if self.min_adr is not None:
                cand &= adr >= self.min_adr
            cand &= (close > self._sma(10)) & (close > self._sma(20)) & (close > self._sma(50))
            prior_low = self._low[-64:].min(axis=0)
            cand &= close / prior_low - 1.0 >= self.min_prior_move
            ret63 = close / self._close[-64] - 1.0
        out: dict[int, _Pending] = {}
        for i in np.flatnonzero(cand):
            highs = self._high[-self.pivot_bars :, i]
            pivot = float(highs.max())
            age = self.pivot_bars - 1 - int(np.flatnonzero(highs == pivot)[-1])
            if age < self.pivot_min_age:
                continue
            tight_low = float(self._low[-self.tight_bars :, i].min())
            if pivot - tight_low > self.max_tight_adr * adr[i] * pivot or tight_low >= pivot:
                continue
            out[i] = _Pending(level=pivot, stop=tight_low, rank=float(ret63[i]))
        return out

    def _ep_setups(self, eligible: np.ndarray) -> dict[int, _Pending]:
        o, h, l, c = self._open_px[-1], self._high[-1], self._low[-1], self._close[-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            gap = o / self._close[-2] - 1.0
            avg_vol = self._volume[-21:-1].mean(axis=0)
            adr_prior = self._adr(end=1)
            cand = (
                eligible
                & (gap >= self.gap_min)
                & (self._volume[-1] >= self.volume_mult * avg_vol)
                & (c >= o)
                & (c - l <= self.ep_max_risk_adr * adr_prior * c)
                & (c > l)
            )
        return {int(i): _Pending(level=float(c[i]), stop=float(l[i]), rank=float(gap[i])) for i in np.flatnonzero(cand)}

    # -- main loop ----------------------------------------------------------------------
    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        signals: list[SignalEvent] = []
        held = {s: int(p.quantity) for s, p in self.portfolio.positions.items() if int(p.quantity) > 0}
        for symbol in list(self._positions):
            if symbol not in held:
                del self._positions[symbol]

        sma_trail = self._sma(self.trail_sma)
        exit_value = 0.0
        for symbol, qty in held.items():
            i = self._index.get(symbol)
            if i is None:
                continue
            state = self._positions.get(symbol)
            if state is None:  # filled on this bar
                pending = self._pending.get(symbol)
                entry = float(self.portfolio.positions[symbol].avg_price)
                stop = pending.stop if pending else float(self._low[-1, i])
                if self.setup == "breakout":
                    stop = max(stop, float(self._low[-1, i]))
                state = _Open(entry=entry, stop=min(stop, entry))
                self._positions[symbol] = state
            elif self._valid[i]:
                state.bars_held += 1

            meta = {"strategy": self.tag, "is_protective": True, **DAY}
            if not self._valid[i]:
                bar = market_event.bars.get(symbol)
                if bar is None or bar.is_stale:
                    signals.append(self.sell_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={**meta, "reason": "NO_DATA"}))
                    exit_value += qty * self._last_close(i)
                continue
            close = float(self._close[-1, i])
            if np.isfinite(sma_trail[i]) and close < sma_trail[i]:
                signals.append(self.sell_moo(timestamp=ts, symbol=symbol, quantity=qty, metadata={**meta, "reason": "TRAIL"}))
                exit_value += qty * close
                continue
            remaining = qty
            if not state.partial_done and state.bars_held >= self.partial_after:
                part = qty // 3
                state.partial_done = True
                state.stop = max(state.stop, state.entry)
                if part > 0:
                    signals.append(self.sell_moo(timestamp=ts, symbol=symbol, quantity=part, metadata={**meta, "reason": "PARTIAL"}))
                    exit_value += part * close
                    remaining -= part
            if remaining > 0:
                signals.append(
                    self.signal(
                        timestamp=ts,
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=remaining,
                        order_type=OrderType.STOP_LOSS,
                        stop_price=state.stop,
                        metadata={**meta, "reason": "STOP"},
                    )
                )

        eligible = self._eligible(ts.date())
        setups = self._breakout_setups(eligible) if self.setup == "breakout" else self._ep_setups(eligible)
        self.setups_seen += len(setups)
        self._pending = {}
        candidates = sorted(
            ((i, p) for i, p in setups.items() if self.symbols[i] not in held), key=lambda item: -item[1].rank
        )
        if not candidates:
            return signals
        equity = self.portfolio.latest_equity
        cash = self.portfolio.cash + exit_value
        for i, pending in candidates:
            risk = pending.level - pending.stop
            if risk <= 0:
                continue
            shares = min(self.risk_per_trade * equity / risk, self.max_weight * equity / pending.level)
            # Leave room for a gap above the level and trading costs.
            quantity = int(min(shares, cash * 0.97 / pending.level))
            if quantity <= 0:
                break
            cash -= quantity * pending.level
            symbol = self.symbols[i]
            self._pending[symbol] = pending
            self.setups_placed += 1
            meta = {"strategy": self.tag, "rank": pending.rank, **DAY}
            if self.setup == "breakout":
                signals.append(
                    self.signal(
                        timestamp=ts,
                        symbol=symbol,
                        side=Side.BUY,
                        quantity=quantity,
                        order_type=OrderType.STOP_LOSS,
                        stop_price=pending.level,
                        metadata=meta,
                    )
                )
            else:
                signals.append(self.buy_moo(timestamp=ts, symbol=symbol, quantity=quantity, metadata=meta))
        return signals

    def _last_close(self, i: int) -> float:
        col = self._close[:, i]
        finite = col[np.isfinite(col)]
        return float(finite[-1]) if len(finite) else 0.0
