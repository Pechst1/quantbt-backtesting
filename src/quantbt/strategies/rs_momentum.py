from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy

# IBD-style relative strength (see quantbt.strategies.minervini): 3/6/9/12-month returns
# weighted 40/20/20/20.
IBD_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


class RelativeStrengthLeadersStrategy(BaseStrategy):
    """
    Concentrated relative-strength leaders in the spirit of William O'Neil and David Ryan
    (Market Wizards), in a fully mechanical form. Rules are fixed in
    reports/rs_momentum/PREREGISTRATION.md.

    On the close of the first trading day of each month:
      - rank index members by 12-1 month momentum (or IBD-style RS),
      - if ``market_symbol`` is set and it closes below its 200-day SMA, sell everything,
      - otherwise hold the top ``top_n``: sell the rest, buy new leaders with an equal slot
        of equity / top_n each (existing holdings are not resized).
    Every day, a holding that closes ``stop_loss_pct`` or more below its entry price is sold
    at the next open; its slot stays in cash until the next signal date.
    All orders are market-on-open for the next bar.
    """

    def __init__(
        self,
        symbols: list[str],
        is_member: Callable[[str, date], bool],
        top_n: int = 10,
        ranking: str = "mom_12_1",
        market_symbol: str | None = "SPY",
        market_sma: int = 200,
        stop_loss_pct: float | None = 0.08,
        dead_after_bars: int = 5,
    ) -> None:
        super().__init__(symbols=symbols)
        if ranking not in ("mom_12_1", "ibd_rs"):
            raise ValueError(f"Unknown ranking {ranking!r}")
        self.is_member = is_member
        self.top_n = top_n
        self.ranking = ranking
        self.market_symbol = market_symbol
        self.market_sma = market_sma
        self.stop_loss_pct = stop_loss_pct
        self.dead_after_bars = dead_after_bars

        self._index = {symbol: i for i, symbol in enumerate(symbols)}
        n = len(symbols)
        self._window = max(253, market_sma)
        self._close = np.full((self._window, n), np.nan)
        self._stale_run = np.zeros(n, dtype=int)
        self._last_month: int | None = None
        self._dead: set[str] = set()
        self._pending_exit: set[str] = set()
        self.rebalances = 0
        self.filter_off_months = 0
        self.last_ranking: dict[str, float] = {}

    def _push(self, market_event: MarketEvent) -> np.ndarray:
        close = np.full(len(self.symbols), np.nan)
        stale = np.zeros(len(self.symbols), dtype=bool)
        for symbol, bar in market_event.bars.items():
            i = self._index.get(symbol)
            if i is None:
                continue
            close[i] = bar.close
            stale[i] = bar.is_stale
        self._close[:-1] = self._close[1:]
        self._close[-1] = close
        self._stale_run = np.where(stale, self._stale_run + 1, 0)
        return stale

    def _score(self) -> np.ndarray:
        c = self._close
        with np.errstate(invalid="ignore", divide="ignore"):
            if self.ranking == "mom_12_1":
                return c[-1 - 21] / c[-1 - 252] - 1.0
            return sum(w * (c[-1] / c[-1 - lag] - 1.0) for lag, w in IBD_WEIGHTS)

    def _market_ok(self) -> bool:
        if self.market_symbol is None:
            return True
        m = self._index.get(self.market_symbol)
        if m is None:
            return False
        window = self._close[-self.market_sma :, m]
        if np.isnan(window).any():
            return False
        return bool(window[-1] > window.mean())

    def _sell(self, ts, symbol: str, qty: int, reason: str) -> SignalEvent:
        self._pending_exit.add(symbol)
        return self.sell_moo(
            timestamp=ts,
            symbol=symbol,
            quantity=qty,
            metadata={"strategy": "RS_LEADERS", "reason": reason},
        )

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        stale = self._push(market_event)
        if self.portfolio is None:
            return []
        ts = market_event.timestamp
        price = self._close[-1]

        holdings = {s: int(p.quantity) for s, p in self.portfolio.positions.items() if int(p.quantity) > 0}
        self._pending_exit &= set(holdings)
        signals: list[SignalEvent] = []
        exit_proceeds = 0.0
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
            if (
                self.stop_loss_pct is not None
                and not stale[i]
                and price[i] <= self.portfolio.positions[symbol].avg_price * (1.0 - self.stop_loss_pct)
            ):
                signals.append(self._sell(ts, symbol, qty, "STOP_LOSS"))
                exit_proceeds += qty * price[i]
                continue
            live[symbol] = qty

        month = ts.month
        is_signal_day = self._last_month is not None and month != self._last_month
        self._last_month = month
        if not is_signal_day:
            return signals
        self.rebalances += 1

        if not self._market_ok():
            self.filter_off_months += 1
            for symbol, qty in live.items():
                signals.append(self._sell(ts, symbol, qty, "MARKET_FILTER"))
            return signals

        as_of = ts.date()
        score = self._score()
        eligible = np.array(
            [
                np.isfinite(score[i]) and not stale[i] and s != self.market_symbol and self.is_member(s, as_of)
                for i, s in enumerate(self.symbols)
            ]
        )
        idx = np.flatnonzero(eligible)
        leaders: list[int] = []
        seen: set[tuple[float, float]] = set()
        for i in idx[np.argsort(-score[idx])]:
            # The membership history sometimes lists one company under two tickers at once
            # (KORS/CPRI, PX/LIN, CCE/CCEP); identical price and score means the same stock.
            key = (round(float(price[i]), 4), round(float(score[i]), 6))
            if key in seen:
                continue
            seen.add(key)
            leaders.append(i)
            if len(leaders) == self.top_n:
                break
        target = [self.symbols[i] for i in leaders]
        self.last_ranking = {self.symbols[i]: float(score[i]) for i in leaders}
        target_set = set(target)

        for symbol, qty in list(live.items()):
            if symbol not in target_set:
                signals.append(self._sell(ts, symbol, qty, "ROTATED_OUT"))
                exit_proceeds += qty * price[self._index[symbol]]
                del live[symbol]

        cash = self.portfolio.cash + exit_proceeds
        slot_value = self.portfolio.latest_equity / self.top_n
        free_slots = self.top_n - len(live)
        for symbol in target:
            if free_slots <= 0:
                break
            if symbol in live or symbol in holdings:
                continue
            i = self._index[symbol]
            # Leave room for the next-open gap and trading costs.
            quantity = int(min(slot_value, cash) * 0.98 // price[i])
            if quantity <= 0:
                break
            cash -= quantity * price[i]
            free_slots -= 1
            signals.append(
                self.buy_moo(
                    timestamp=ts,
                    symbol=symbol,
                    quantity=quantity,
                    metadata={"strategy": "RS_LEADERS", "score": float(score[i])},
                )
            )
        return signals
