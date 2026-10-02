from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy

# IBD-style relative strength: 12-month price performance with the latest quarter
# double-weighted (40/20/20/20 over 63/126/189/252 trading days). IBD's exact formula
# is proprietary; this is the commonly published approximation.
RS_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


class MinerviniTrendTemplateStrategy(BaseStrategy):
    """
    Mark Minervini's Trend Template (Stock Market Wizards; Trade Like a Stock Market
    Wizard, ch. 5), long only, applied to point-in-time index members.

    A stock qualifies on a close when all eight published criteria hold:
      1. price above the 150-day and the 200-day moving average
      2. 150-day MA above the 200-day MA
      3. 200-day MA rising for at least one month (above its value 21 bars ago)
      4. 50-day MA above both the 150-day and the 200-day MA
      5. price above the 50-day MA
      6. price at least 30% above its 52-week low
      7. price within 25% of its 52-week high
      8. relative strength rank of at least 70 (percentile within the universe)

    Entries: qualifying stocks that are index members fill free slots in RS order,
    one equal-weight slot each, bought at the next open.
    Exits at the next open when the close is 8% or more below the entry price
    (Minervini caps losses at 10% and averages 6-7%; O'Neil's rule is 7-8%), or the
    close falls below the 50-day MA (his Stage 2 sell rule).

    Missing data: a bar the data handler forward-filled (no real print) is skipped,
    so it never enters the indicator windows and triggers no decision. A held stock
    is sold at the close of its final real bar when ``last_bar_dates`` says that is
    the day its listing ends (acquisitions and delistings are announced in advance);
    a temporary gap does not force an exit.

    Optional market filter: when ``market_symbol`` is set, new entries need that
    symbol to pass criteria 1-5 itself.
    """

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        max_positions: int = 10,
        min_rs_rank: float = 70.0,
        stop_loss_pct: float = 0.08,
        market_symbol: str | None = None,
        last_bar_dates: dict[str, date] | None = None,
    ) -> None:
        super().__init__(symbols=symbols)
        self.is_member = is_member
        self.max_positions = max_positions
        self.min_rs_rank = min_rs_rank
        self.stop_loss_pct = stop_loss_pct
        self.market_symbol = market_symbol
        self.last_bar_dates = dict(last_bar_dates or {})

        self._index = {symbol: i for i, symbol in enumerate(symbols)}
        n = len(symbols)
        self._window = 253
        self._close = np.full((self._window, n), np.nan)
        self._high = np.full((self._window, n), np.nan)
        self._low = np.full((self._window, n), np.nan)
        self._sma200_hist = np.full((22, n), np.nan)
        self.last_screen: dict[str, float] = {}
        # Exits already sent but not yet filled, so a missing next bar can't re-send them.
        self._pending_exits: set[str] = set()

    def _push(self, market_event: MarketEvent) -> np.ndarray:
        close = np.full(len(self.symbols), np.nan)
        high = close.copy()
        low = close.copy()
        fresh = np.zeros(len(self.symbols), dtype=bool)
        for symbol, bar in market_event.bars.items():
            i = self._index.get(symbol)
            if i is None:
                continue
            close[i], high[i], low[i] = bar.close, bar.high, bar.low
            # The data handler forward-fills symbols without a real bar (gaps, delistings).
            stale = getattr(bar, "is_stale", None)
            if stale is None:
                stale = bar.volume <= 0 and bar.open == bar.high == bar.low == bar.close
            fresh[i] = not stale
        # Windows count a symbol's own trading bars: only columns with a real bar advance.
        for buf, row in ((self._close, close), (self._high, high), (self._low, low)):
            buf[:-1, fresh] = buf[1:, fresh]
            buf[-1, fresh] = row[fresh]
        return fresh

    def _template(self, fresh: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        c = self._close
        price = c[-1]
        sma50 = c[-50:].mean(axis=0)
        sma150 = c[-150:].mean(axis=0)
        sma200 = c[-200:].mean(axis=0)
        self._sma200_hist[:-1, fresh] = self._sma200_hist[1:, fresh]
        self._sma200_hist[-1, fresh] = sma200[fresh]
        sma200_month_ago = self._sma200_hist[0]
        low52 = self._low[-252:].min(axis=0)
        high52 = self._high[-252:].max(axis=0)

        with np.errstate(invalid="ignore"):
            structure = (
                (price > sma150)
                & (price > sma200)
                & (sma150 > sma200)
                & (sma200 > sma200_month_ago)
                & (sma50 > sma150)
                & (sma50 > sma200)
                & (price > sma50)
            )
            range_ok = (price >= 1.30 * low52) & (price >= 0.75 * high52)
            rs_score = sum(w * (price / c[-1 - lag] - 1.0) for lag, w in RS_WEIGHTS)
        return structure, range_ok, rs_score, sma50

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        fresh = self._push(market_event)
        structure, range_ok, rs_score, sma50 = self._template(fresh)
        if self.portfolio is None:
            return []

        as_of = market_event.timestamp.date()
        price = self._close[-1]
        members = np.array([self.is_member(s, as_of) for s in self.symbols])
        ranked = members & np.isfinite(rs_score) & fresh
        rs_rank = np.full(len(self.symbols), np.nan)
        if ranked.any():
            scores = rs_score[ranked]
            order = scores.argsort().argsort()
            rs_rank[ranked] = 100.0 * (order + 1) / len(scores)

        signals: list[SignalEvent] = []
        held: list[str] = []
        open_symbols: set[str] = set()
        unfilled_exits = 0
        exit_proceeds = 0.0
        self._pending_exits = {
            s for s in self._pending_exits if self.portfolio.position_for_symbol(s).quantity > 0
        }
        for symbol, position in self.portfolio.positions.items():
            qty = int(position.quantity)
            if qty <= 0:
                continue
            open_symbols.add(symbol)
            if symbol in self._pending_exits:
                unfilled_exits += 1
                continue
            i = self._index.get(symbol)
            if i is None:
                continue
            if not fresh[i]:
                # No real print today: hold and decide on the next real bar.
                held.append(symbol)
                continue
            order = self.sell_moo
            reason = None
            if self.last_bar_dates.get(symbol) == as_of:
                order, reason = self.sell_moc, "DELISTED"
            elif price[i] <= position.avg_price * (1.0 - self.stop_loss_pct):
                reason = "STOP_LOSS"
            elif price[i] < sma50[i]:
                reason = "BELOW_50DMA"
            if reason is None:
                held.append(symbol)
                continue
            exit_proceeds += qty * price[i]
            self._pending_exits.add(symbol)
            signals.append(
                order(
                    timestamp=market_event.timestamp,
                    symbol=symbol,
                    quantity=qty,
                    metadata={"strategy": "MINERVINI_TT", "reason": reason},
                )
            )

        qualifies = structure & range_ok & (rs_rank >= self.min_rs_rank) & ranked
        self.last_screen = {self.symbols[i]: float(rs_rank[i]) for i in np.flatnonzero(qualifies)}

        if self.market_symbol is not None:
            m = self._index.get(self.market_symbol)
            if m is None or not bool(structure[m]):
                return signals

        free_slots = self.max_positions - len(held) - unfilled_exits
        if free_slots <= 0:
            return signals
        equity = self.portfolio.latest_equity
        cash = self.portfolio.cash + exit_proceeds
        slot_value = equity / self.max_positions
        # Held names, including ones exiting on this bar, are never bought again on the same bar.
        candidates = sorted(
            (
                i
                for i in np.flatnonzero(qualifies)
                if self.symbols[i] not in open_symbols
                and self.last_bar_dates.get(self.symbols[i], date.max) > as_of
            ),
            key=lambda i: -rs_score[i],
        )
        for i in candidates[:free_slots]:
            budget = min(slot_value, cash)
            # Leave room for the next-open gap and trading costs.
            quantity = int(budget * 0.98 // price[i])
            if quantity <= 0:
                break
            cash -= quantity * price[i]
            signals.append(
                self.buy_moo(
                    timestamp=market_event.timestamp,
                    symbol=self.symbols[i],
                    quantity=quantity,
                    metadata={"strategy": "MINERVINI_TT", "rs_rank": float(rs_rank[i])},
                )
            )
        return signals
