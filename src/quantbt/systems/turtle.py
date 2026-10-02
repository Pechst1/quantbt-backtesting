"""Turtle Trading System 1 and System 2, as published by Curtis Faith.

Source: Curtis Faith, "The Original Turtle Trading Rules" (2003) and "Way of the
Turtle" (2007). Every parameter below is the published value; nothing is fitted.

- N: 20-day Wilder average of the true range, N = (19 * prior N + TR) / 20.
- Unit: 1% of (notional) account equity per N of daily price movement.
- System 1: enter on a break of the prior 20-day high/low; skip the entry if the
  last 20-day breakout (taken or not) would have been a winner; if skipped, enter
  on the 55-day failsafe breakout. Exit on a break of the prior 10-day low/high.
- System 2: enter on every break of the prior 55-day high/low; exit on a break of
  the prior 20-day low/high.
- Stops: 2N from the entry price; when a unit is added, all stops move to 2N from
  the latest fill.
- Pyramiding: add one unit every 1/2 N beyond the previous fill, up to 4 units.
- Limits: 4 units per market, 6 in closely correlated markets, 10 in loosely
  correlated markets, 12 per direction.
- Losses: for every 10% the account is down from its start-of-year value, size as
  if the account were 20% smaller (compounded per step).
- Orders are intraday stops at the breakout, add, stop and exit levels. A breakout
  needs price to trade beyond the channel, not just touch it. A gap through a
  level fills at the open.

Daily bars hide the intraday path, so each bar is resolved pessimistically:
favourable-side orders (entries and adds) fill first, then adverse-side orders
(stops and exits) are checked against the same bar with the updated stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantbt.systems.data import PricePanel, daily_cash_rate
from quantbt.systems.overlay import NativeVolEstimator, VolTarget, capped_scale, financing_rate

# ETF proxies for the markets the Turtles traded (Faith, ch. "Markets"): US bonds and
# notes, the CME currencies, the S&P 500, COMEX metals, NYMEX energy and the softs.
# Eurodollar / 90-day bill futures have no ETF proxy and are left out; the softs
# (coffee, cocoa, sugar, cotton) are represented by the DBA agriculture basket.
# Values: (closely correlated group, loosely correlated group).
TURTLE_UNIVERSE: dict[str, tuple[str, str]] = {
    "TLT": ("us_rates", "rates"),
    "IEF": ("us_rates", "rates"),
    "FXE": ("european_fx", "fx"),
    "FXF": ("european_fx", "fx"),
    "FXB": ("sterling", "fx"),
    "FXY": ("yen", "fx"),
    "FXC": ("cad", "fx"),
    "SPY": ("us_equity", "equity"),
    "GLD": ("precious", "metals"),
    "SLV": ("precious", "metals"),
    "DBB": ("base_metals", "metals"),
    "USO": ("energy", "energy"),
    "UGA": ("energy", "energy"),
    "DBA": ("agriculture", "agriculture"),
}


@dataclass(frozen=True, slots=True)
class TurtleConfig:
    system: int = 1
    n_period: int = 20
    risk_per_unit: float = 0.01
    stop_n: float = 2.0
    add_every_n: float = 0.5
    max_units_market: int = 4
    max_units_close: int = 6
    max_units_loose: int = 10
    max_units_direction: int = 12
    drawdown_step: float = 0.10
    notional_cut: float = 0.20
    initial_equity: float = 1_000_000.0
    cost_bps_per_side: float = 9.5
    cash_symbol: str | None = "BIL"
    borrow_spread_bps: float = 0.0

    @property
    def entry_lookback(self) -> int:
        return 20 if self.system == 1 else 55

    @property
    def exit_lookback(self) -> int:
        return 10 if self.system == 1 else 20

    @property
    def failsafe_lookback(self) -> int | None:
        return 55 if self.system == 1 else None


@dataclass(slots=True)
class _Position:
    direction: int
    entry_n: float
    unit_qty: list[float] = field(default_factory=list)
    unit_px: list[float] = field(default_factory=list)
    stop: float = 0.0
    entry_date: pd.Timestamp | None = None

    @property
    def units(self) -> int:
        return len(self.unit_qty)

    @property
    def qty(self) -> int:
        return self.direction * sum(self.unit_qty)

    @property
    def last_fill(self) -> float:
        return self.unit_px[-1]


@dataclass(slots=True)
class _Shadow:
    """Hypothetical one-unit System 1 trade used by the last-breakout filter."""

    direction: int
    entry: float
    stop: float


@dataclass(slots=True)
class TurtleResult:
    equity: pd.Series
    gross_leverage: pd.Series
    trades: pd.DataFrame
    config: TurtleConfig
    scale: pd.Series | None = None


def wilder_n(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Turtle N: first value is the simple mean of `period` true ranges, then Wilder smoothing."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    values = tr.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        out[period - 1] = values[:period].mean()
        for i in range(period, len(values)):
            out[i] = ((period - 1) * out[i - 1] + values[i]) / period
    return pd.Series(out, index=high.index)


def _symbol_indicators(panel: PricePanel, symbol: str, config: TurtleConfig) -> pd.DataFrame:
    """Per-symbol levels known before the open of each bar (all shifted by one bar)."""
    frame = pd.DataFrame(
        {
            "open": panel.open[symbol],
            "high": panel.high[symbol],
            "low": panel.low[symbol],
            "close": panel.close[symbol],
        }
    ).dropna()
    out = pd.DataFrame(index=frame.index)
    out["n"] = wilder_n(frame["high"], frame["low"], frame["close"], config.n_period).shift(1)
    out["entry_hi"] = frame["high"].rolling(config.entry_lookback).max().shift(1)
    out["entry_lo"] = frame["low"].rolling(config.entry_lookback).min().shift(1)
    out["exit_hi"] = frame["high"].rolling(config.exit_lookback).max().shift(1)
    out["exit_lo"] = frame["low"].rolling(config.exit_lookback).min().shift(1)
    if config.failsafe_lookback:
        out["fail_hi"] = frame["high"].rolling(config.failsafe_lookback).max().shift(1)
        out["fail_lo"] = frame["low"].rolling(config.failsafe_lookback).min().shift(1)
    return out.reindex(panel.index)


def _favourable_fill(direction: int, level: float, bar_open: float) -> float:
    """Buy stop fills at max(open, level); sell stop at min(open, level)."""
    return max(bar_open, level) if direction > 0 else min(bar_open, level)


def _adverse_fill(direction: int, level: float, bar_open: float) -> float:
    """Exit of a long (a sell stop) fills at min(open, level); exit of a short at max."""
    return min(bar_open, level) if direction > 0 else max(bar_open, level)


def run_turtle(
    panel: PricePanel,
    config: TurtleConfig | None = None,
    groups: dict[str, tuple[str, str]] | None = None,
    vol_target: VolTarget | None = None,
) -> TurtleResult:
    """Simulate the system.

    Without `vol_target` the account trades the published book. With it, the
    published rules (minus the drawdown rule, which the overlay replaces) define a
    native book of fractional units sized on current equity, and the account holds
    `k` times it: rescaled at each open, with intraday entries and adds trimmed so
    gross exposure stays within the cap (see `overlay`).
    """
    config = config or TurtleConfig()
    if config.system not in (1, 2):
        raise ValueError("system must be 1 or 2")
    groups = groups if groups is not None else TURTLE_UNIVERSE
    symbols = [s for s in panel.symbols if s in groups]
    if not symbols:
        raise ValueError("None of the panel symbols are in the Turtle universe.")
    cost = config.cost_bps_per_side / 10_000.0
    rf = daily_cash_rate(panel, config.cash_symbol)
    overlay = vol_target is not None

    ind = {s: _symbol_indicators(panel, s, config) for s in symbols}
    opens = panel.open[symbols].to_numpy()
    highs = panel.high[symbols].to_numpy()
    lows = panel.low[symbols].to_numpy()
    closes = panel.close[symbols].to_numpy()
    last_close = np.full(len(symbols), np.nan)

    cash = config.initial_equity
    held = np.zeros(len(symbols))  # shares actually held; equals the native book without an overlay
    positions: dict[str, _Position] = {}
    shadows: dict[str, _Shadow] = {}
    last_breakout_winner: dict[str, bool] = {s: False for s in symbols}
    trades: list[dict] = []
    equity_hist: list[float] = []
    lev_hist: list[float] = []
    scale_hist: list[float] = []

    equity = config.initial_equity
    year_start_equity = equity
    current_year: int | None = None
    estimator = NativeVolEstimator(vol_target) if overlay else None
    scale = 0.0
    k = 1.0
    native_cash = 0.0
    native_value = 0.0

    def units_in(direction: int, key: int | None = None, value: str | None = None) -> int:
        total = 0
        for sym, pos in positions.items():
            if pos.direction != direction:
                continue
            if key is not None and groups[sym][key] != value:
                continue
            total += pos.units
        return total

    def can_add(sym: str, direction: int) -> bool:
        close_g, loose_g = groups[sym]
        return (
            units_in(direction, 0, close_g) < config.max_units_close
            and units_in(direction, 1, loose_g) < config.max_units_loose
            and units_in(direction) < config.max_units_direction
        )

    def unit_size(n: float, notional: float) -> float:
        if not (n > 0) or notional <= 0:
            return 0
        size = config.risk_per_unit * notional / n
        return size if overlay else int(math.floor(size))

    def trade(j: int, delta: float, px: float) -> None:
        nonlocal cash
        if delta != 0.0:
            cash -= delta * px + abs(delta * px) * cost
            held[j] += delta

    def native_fill(j: int, delta: float, px: float, marks: np.ndarray, prior_equity: float) -> None:
        """A published-rule trade; the account mirrors it scaled by `k`, entries capped."""
        nonlocal native_cash
        if not overlay:
            trade(j, delta, px)
            return
        native_cash -= delta * px + abs(delta * px) * cost
        want = k * delta
        others = float(np.nansum(np.abs(np.delete(held, j) * np.delete(marks, j))))
        room = max(0.0, vol_target.max_gross * prior_equity - others) / px
        new_qty = held[j] + want
        if abs(new_qty) > room:
            # Fill only up to the cap; never cut what is already held (the next open does that).
            new_qty = math.copysign(max(room, abs(held[j])), new_qty)
        trade(j, new_qty - held[j], px)

    for t, ts in enumerate(panel.index):
        prior_equity = equity
        if overlay:
            notional = prior_equity
        else:
            if current_year != ts.year:
                current_year = ts.year
                year_start_equity = equity
            drawdown = max(0.0, 1.0 - equity / year_start_equity) if year_start_equity > 0 else 1.0
            steps = int(math.floor(drawdown / config.drawdown_step + 1e-12))
            notional = year_start_equity * (1.0 - config.notional_cut) ** steps

        cash *= 1.0 + financing_rate(cash, float(rf.iloc[t]), config.borrow_spread_bps)
        marks = np.where(np.isnan(opens[t]), last_close, opens[t])

        if overlay:
            # Rescale the account to k times the native book at the open.
            native_qty = np.array([positions[s].qty if s in positions else 0.0 for s in symbols])
            k = capped_scale(scale, float(np.nansum(np.abs(native_qty * marks))), prior_equity, vol_target.max_gross)
            for j in range(len(symbols)):
                if not np.isnan(opens[t, j]):
                    trade(j, k * native_qty[j] - held[j], opens[t, j])

        for j, sym in enumerate(symbols):
            o, h, lo, c = opens[t, j], highs[t, j], lows[t, j], closes[t, j]
            if np.isnan(c):
                continue
            row = ind[sym].iloc[t]
            n = row["n"]
            ready = not (np.isnan(n) or np.isnan(row["entry_hi"]) or np.isnan(row["exit_hi"]))

            # --- System 1 last-breakout filter: resolve the hypothetical trade on this bar.
            if config.system == 1 and ready:
                shadow = shadows.get(sym)
                if shadow is None:
                    if h > row["entry_hi"] and not lo < row["entry_lo"]:
                        d = 1
                    elif lo < row["entry_lo"] and not h > row["entry_hi"]:
                        d = -1
                    else:
                        d = 0
                    if d:
                        level = row["entry_hi"] if d > 0 else row["entry_lo"]
                        px = _favourable_fill(d, level, o)
                        shadows[sym] = _Shadow(direction=d, entry=px, stop=px - d * config.stop_n * n)
                        shadow_new = True
                    else:
                        shadow_new = False
                else:
                    shadow_new = False
                shadow = shadows.get(sym)
                filter_winner_before_today = last_breakout_winner[sym]
                if shadow is not None:
                    d = shadow.direction
                    exit_level = row["exit_lo"] if d > 0 else row["exit_hi"]
                    stop_hit = lo <= shadow.stop if d > 0 else h >= shadow.stop
                    exit_hit = lo <= exit_level if d > 0 else h >= exit_level
                    if stop_hit or exit_hit:
                        if stop_hit and exit_hit:
                            level = max(shadow.stop, exit_level) if d > 0 else min(shadow.stop, exit_level)
                        else:
                            level = shadow.stop if stop_hit else exit_level
                        px = _adverse_fill(d, level, o)
                        last_breakout_winner[sym] = d * (px - shadow.entry) > 0
                        del shadows[sym]
            else:
                shadow_new = False
                filter_winner_before_today = False

            pos = positions.get(sym)

            # --- Entries and adds (favourable side first).
            if pos is None and ready:
                direction = 0
                level = float("nan")
                long_break = h > row["entry_hi"]
                short_break = lo < row["entry_lo"]
                if long_break != short_break:
                    direction = 1 if long_break else -1
                    level = row["entry_hi"] if long_break else row["entry_lo"]
                    if config.system == 1 and (filter_winner_before_today or not shadow_new):
                        # Skipped (last breakout won) or not a fresh breakout under the filter's
                        # hypothetical trade: only the 55-day failsafe can enter.
                        direction = 0
                if direction == 0 and config.system == 1 and not np.isnan(row["fail_hi"]):
                    fail_long = h > row["fail_hi"]
                    fail_short = lo < row["fail_lo"]
                    if fail_long != fail_short:
                        direction = 1 if fail_long else -1
                        level = row["fail_hi"] if fail_long else row["fail_lo"]
                if direction and can_add(sym, direction):
                    qty = unit_size(n, notional)
                    if qty > 0:
                        px = _favourable_fill(direction, level, o)
                        native_fill(j, direction * qty, px, marks, prior_equity)
                        pos = _Position(direction=direction, entry_n=float(n), entry_date=ts)
                        pos.unit_qty.append(qty)
                        pos.unit_px.append(px)
                        pos.stop = px - direction * config.stop_n * pos.entry_n
                        positions[sym] = pos

            if pos is not None:
                d = pos.direction
                while pos.units < config.max_units_market and can_add(sym, d):
                    add_level = pos.last_fill + d * config.add_every_n * pos.entry_n
                    if not (h >= add_level if d > 0 else lo <= add_level):
                        break
                    qty = unit_size(n if not np.isnan(n) else pos.entry_n, notional)
                    if qty <= 0:
                        break
                    px = _favourable_fill(d, add_level, o)
                    native_fill(j, d * qty, px, marks, prior_equity)
                    pos.unit_qty.append(qty)
                    pos.unit_px.append(px)
                    pos.stop = px - d * config.stop_n * pos.entry_n

                # --- Stops and channel exits (adverse side), pessimistically on the same bar.
                exit_level = row["exit_lo"] if d > 0 else row["exit_hi"]
                stop_hit = lo <= pos.stop if d > 0 else h >= pos.stop
                exit_hit = (not np.isnan(exit_level)) and (lo <= exit_level if d > 0 else h >= exit_level)
                if stop_hit or exit_hit:
                    if stop_hit and exit_hit:
                        level = max(pos.stop, exit_level) if d > 0 else min(pos.stop, exit_level)
                        reason = "stop" if level == pos.stop else "exit"
                    else:
                        level = pos.stop if stop_hit else exit_level
                        reason = "stop" if stop_hit else "exit"
                    px = _adverse_fill(d, level, o)
                    if overlay:
                        native_cash += pos.qty * px - abs(pos.qty * px) * cost
                    trade(j, -held[j], px)
                    gross_pnl = d * sum(q * (px - p) for q, p in zip(pos.unit_qty, pos.unit_px))
                    trades.append(
                        {
                            "symbol": sym,
                            "direction": d,
                            "entry_date": pos.entry_date,
                            "exit_date": ts,
                            "units": pos.units,
                            "entry_price": pos.unit_px[0],
                            "exit_price": px,
                            "pnl_before_costs": gross_pnl,
                            "exit_reason": reason,
                        }
                    )
                    del positions[sym]

            last_close[j] = c

        values = np.where(np.isnan(last_close), 0.0, held * np.nan_to_num(last_close))
        equity = cash + values.sum()
        if overlay:
            value = native_cash + sum(pos.qty * last_close[symbols.index(s)] for s, pos in positions.items())
            if prior_equity > 0:
                estimator.update((value - native_value) / prior_equity)
            native_value = value
            scale = estimator.scale()
        equity_hist.append(equity)
        lev_hist.append(np.abs(values).sum() / equity if equity > 0 else np.nan)
        scale_hist.append(k * (1.0 if overlay else 0.0))
        if equity <= 0:
            break

    index = panel.index[: len(equity_hist)]
    return TurtleResult(
        equity=pd.Series(equity_hist, index=index, name=f"turtle_s{config.system}"),
        gross_leverage=pd.Series(lev_hist, index=index),
        trades=pd.DataFrame(trades),
        config=config,
        scale=pd.Series(scale_hist, index=index),
    )
