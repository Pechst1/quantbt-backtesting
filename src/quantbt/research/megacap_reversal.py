"""
Megacap reversal with Cook breadth and a trend filter, costed like Scalable Capital.

Rules and variants are fixed in reports/megacap_reversal/PREREGISTRATION.md. The core is the
long-only 4-week reversal book from PR #10 (H4 R4) on the largest S&P 500 members by dollar
volume; exposure is switched by a weekly trend check and Mark Cook's breadth buy state (PR #11).

Timing follows the project's conventions: decisions on a close, trades at the next open.
Costs are a fixed fee per order plus a half-spread on traded notional, at a fixed account size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --------------------------------------------------------------------------- data


@dataclass
class VolumePanel:
    """Adjusted open/close, dollar volume and point-in-time membership, all the same shape."""

    open: pd.DataFrame
    close: pd.DataFrame
    dollar_volume: pd.DataFrame
    member: pd.DataFrame


def load_volume_panel(
    prices_path: str | Path,
    membership_csv: str | Path,
    start: str = "1995-01-01",
    end: str | None = None,
) -> VolumePanel:
    cols = ["date", "ticker", "open", "close", "adj_close", "volume"]
    frame = pd.read_parquet(prices_path, columns=cols)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    frame = frame[(frame["close"] > 0) & (frame["open"] > 0) & frame["adj_close"].notna()]
    factor = frame["adj_close"] / frame["close"]
    frame = frame.assign(
        dv=frame["close"] * frame["volume"].fillna(0.0),
        open=frame["open"] * factor,
        close=frame["adj_close"],
    )
    open_ = frame.pivot(index="date", columns="ticker", values="open").sort_index()
    close = frame.pivot(index="date", columns="ticker", values="close").reindex_like(open_)
    dv = frame.pivot(index="date", columns="ticker", values="dv").reindex_like(open_)

    member = pd.DataFrame(False, index=close.index, columns=close.columns)
    spells = pd.read_csv(membership_csv, parse_dates=["effective_from", "effective_to"])
    for row in spells.itertuples():
        if row.symbol not in member.columns:
            continue
        stop = row.effective_to if pd.notna(row.effective_to) else member.index[-1]
        member.loc[(member.index >= row.effective_from) & (member.index <= stop), row.symbol] = True
    return VolumePanel(open=open_, close=close, dollar_volume=dv, member=member)


# --------------------------------------------------------------------------- signals


def cook_breadth_state(close: pd.DataFrame, member: pd.DataFrame, quiet_band: float = 400 / 3000,
                       sum_window: int = 21, min_history: int = 504, low_pct: float = 5.0) -> pd.DataFrame:
    """
    PR #11's Cook buy state, vectorised. b = (adv - dec) / n over members with a close today and
    yesterday; quiet days count 0; C = 21-day sum; buy starts when C < the 5th percentile of C's
    own past and ends on the first close with C >= the past median.
    """
    prev = close.shift(1)
    ok = member & close.notna() & prev.notna()
    diff = (close - prev).where(ok)
    n = ok.sum(axis=1)
    b = ((diff > 0).sum(axis=1) - (diff < 0).sum(axis=1)) / n.where(n > 0)
    b = b[n >= 50]
    x = b.where(b.abs() > quiet_band, 0.0)
    c = x.rolling(sum_window).sum().dropna()
    values = c.to_numpy()
    state = np.zeros(len(values), dtype=bool)
    ready = np.zeros(len(values), dtype=bool)
    on = False
    for i in range(len(values)):
        if i < min_history:
            continue
        hist = values[:i]
        low, mid = np.percentile(hist, [low_pct, 50.0])
        if on and values[i] >= mid:
            on = False
        if not on and values[i] < low:
            on = True
        state[i] = on
        ready[i] = True
    out = pd.DataFrame({"breadth": b.reindex(c.index), "cum_breadth": c, "buy": state, "ready": ready})
    return out


def trend_signals(spy: pd.Series, tbill: pd.Series) -> pd.DataFrame:
    """200-day rule and 12-month time-series momentum on SPY total return (True = in)."""
    sma = spy.rolling(200).mean()
    tb_growth = (1.0 + tbill.reindex(spy.index).fillna(0.0)).cumprod()
    excess = spy / spy.shift(252) - tb_growth / tb_growth.shift(252)
    return pd.DataFrame({"200d": (spy > sma) & sma.notna(), "tsmom": (excess > 0) & excess.notna()})


def daily_tbill(irx: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily T-bill accrual from ^IRX (annual %), using the previous day's quote."""
    rate = irx.reindex(index.union(irx.index)).ffill().reindex(index).shift(1).fillna(0.0)
    return rate.clip(lower=0.0) / 100.0 / TRADING_DAYS


# --------------------------------------------------------------------------- simulator


@dataclass
class MegacapConfig:
    top_n: int = 100
    cohorts: int = 4
    lookback: int = 5
    dv_window: int = 63
    trend: str = "200d"  # "none", "200d" or "tsmom"
    cook: bool = True  # be in while Cook's buy state is on, even if the trend is off
    cook_leverage: float = 1.0  # gross exposure while Cook's buy state is on
    band: float = 0.25
    fee: float = 1.99  # per order, account currency
    spread_bps: float = 6.0  # half-spread per side
    borrow_spread: float = 0.03  # over T-bills, per year
    account: float = 25_000.0


@dataclass
class Inputs:
    """Everything the simulator needs, precomputed once for all variants."""

    index: pd.DatetimeIndex
    names: pd.Index
    open: np.ndarray
    close: np.ndarray  # raw adjusted close (NaN on missing days)
    mark: np.ndarray  # forward-filled close
    rel: np.ndarray  # lookback return minus SPY's
    dv: np.ndarray  # average dollar volume
    eligible: np.ndarray  # member & history & finite inputs
    last_row: np.ndarray
    weekly: np.ndarray  # last trading day of each week
    trend: dict[str, np.ndarray]
    cook: np.ndarray
    tbill: np.ndarray


def build_inputs(panel: VolumePanel, spy: pd.Series, tbill: pd.Series, cook: pd.Series,
                 lookback: int = 5, dv_window: int = 63) -> Inputs:
    close = panel.close
    index = close.index
    spy = spy.reindex(index).ffill()
    rel = close.pct_change(lookback, fill_method=None).sub(spy.pct_change(lookback), axis=0)
    dv = panel.dollar_volume.rolling(dv_window, min_periods=dv_window).mean()
    history = close.notna().cumsum()
    eligible = panel.member & (history >= dv_window) & close.notna() & rel.notna() & dv.notna()
    last_valid = close.apply(lambda s: s.last_valid_index())
    last_row = index.get_indexer(pd.DatetimeIndex(last_valid.values))
    period = index.to_period("W-FRI")
    weekly = np.r_[period[1:] != period[:-1], True]
    tb = tbill.reindex(index).fillna(0.0)
    trends = trend_signals(spy, tb)
    return Inputs(
        index=index,
        names=close.columns,
        open=panel.open.to_numpy(),
        close=close.to_numpy(),
        mark=close.ffill().to_numpy(),
        rel=rel.to_numpy(),
        dv=dv.to_numpy(),
        eligible=eligible.to_numpy(),
        last_row=last_row,
        weekly=weekly,
        trend={k: trends[k].to_numpy() for k in trends},
        cook=cook.reindex(index).fillna(False).astype(bool).to_numpy(),
        tbill=tb.to_numpy(),
    )


def select(inp: Inputs, i: int, top_n: int) -> np.ndarray:
    """Bottom decile by relative return within the top-N members by dollar volume on close i."""
    cols = np.flatnonzero(inp.eligible[i])
    if len(cols) < top_n:
        return np.array([], dtype=int)
    big = cols[np.argsort(-inp.dv[i, cols], kind="stable")[:top_n]]
    order = big[np.argsort(inp.rel[i, big], kind="stable")]
    return order[: max(1, top_n // 10)]


def top_universe(inp: Inputs, i: int, top_n: int) -> np.ndarray:
    cols = np.flatnonzero(inp.eligible[i])
    return cols[np.argsort(-inp.dv[i, cols], kind="stable")[:top_n]]


def _book(cohorts: list[np.ndarray], k: int, width: int) -> np.ndarray:
    w = np.zeros(width)
    for cohort in cohorts:
        if len(cohort):
            w[cohort] += 1.0 / (k * len(cohort))
    return w


@dataclass
class RunResult:
    equity: pd.Series
    exposure: pd.Series
    orders: int
    fees: float
    spread_cost: float
    days_cook: int


def run_megacap(inp: Inputs, cfg: MegacapConfig, start: str, end: str) -> RunResult:
    idx = inp.index
    s = int(idx.searchsorted(pd.Timestamp(start)))
    e = int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    width = len(inp.names)
    spread = cfg.spread_bps / 10_000.0
    borrow = cfg.borrow_spread / TRADING_DAYS

    shares = np.zeros(width)
    cash = cfg.account
    cohorts: list[np.ndarray] = []
    trend_on = False
    invested = False
    lev = 1.0
    pending: np.ndarray | None = None
    orders = 0
    fees = 0.0
    spread_cost = 0.0
    days_cook = 0
    equity = np.full(e - s + 1, np.nan)
    exposure = np.zeros(e - s + 1)

    for i in range(s, e + 1):
        tb = inp.tbill[i]
        cash += cash * tb if cash >= 0 else cash * (tb + borrow)

        # ---- open: trade toward the pending target weights
        if pending is not None:
            px = np.where(np.isfinite(inp.open[i]), inp.open[i], np.nan)
            mark = np.where(np.isfinite(px), px, inp.mark[i - 1])
            held_val = np.nansum(shares * mark)
            value = cash + held_val
            cur = np.nan_to_num(shares * mark)
            target = pending * value
            tradable = np.isfinite(px)
            sell_all = (target == 0) & (shares != 0)
            buy_new = (target > 0) & (shares == 0)
            adjust = (target > 0) & (shares != 0) & (np.abs(target - cur) > cfg.band * target)
            trade = tradable & (sell_all | buy_new | adjust)
            for col in np.flatnonzero(trade):
                delta = target[col] - cur[col]
                cost = cfg.fee + spread * abs(delta)
                shares[col] += delta / px[col]
                if sell_all[col]:
                    shares[col] = 0.0
                cash -= delta + cost
                orders += 1
                fees += cfg.fee
                spread_cost += spread * abs(delta)
            pending = None

        # ---- close: names leaving the data are sold at their last close
        for col in np.flatnonzero(shares != 0):
            if inp.last_row[col] == i and i < len(idx) - 1:
                proceeds = shares[col] * inp.close[i, col]
                cash += proceeds - cfg.fee - spread * abs(proceeds)
                orders += 1
                fees += cfg.fee
                spread_cost += spread * abs(proceeds)
                shares[col] = 0.0
        held_val = float(np.nansum(shares * inp.mark[i]))
        value = cash + held_val
        equity[i - s] = value
        exposure[i - s] = held_val / value if value > 0 else 0.0
        if i == e:
            break

        # ---- decisions on this close
        cook_on = cfg.cook and inp.cook[i]
        days_cook += bool(cook_on)
        if inp.weekly[i]:
            trend_on = True if cfg.trend == "none" else bool(inp.trend[cfg.trend][i])
            cohorts = (cohorts + [select(inp, i, cfg.top_n)])[-cfg.cohorts:]
        want_in = trend_on or cook_on
        want_lev = cfg.cook_leverage if cook_on else 1.0
        if i == s:
            trend_on = True if cfg.trend == "none" else bool(inp.trend[cfg.trend][i])
            want_in = trend_on or cook_on
        if want_in and not invested:
            cohorts = [select(inp, i, cfg.top_n)] * cfg.cohorts
            pending = _book(cohorts, cfg.cohorts, width) * want_lev
        elif not want_in and invested:
            pending = np.zeros(width)
        elif want_in and (inp.weekly[i] or want_lev != lev):
            pending = _book(cohorts, cfg.cohorts, width) * want_lev
        invested = want_in
        lev = want_lev if want_in else 1.0

    dates = idx[s : e + 1]
    return RunResult(
        equity=pd.Series(equity, index=dates, name="equity"),
        exposure=pd.Series(exposure, index=dates, name="exposure"),
        orders=orders,
        fees=fees,
        spread_cost=spread_cost,
        days_cook=days_cook,
    )


def equal_weight_megacaps(inp: Inputs, top_n: int, start: str, end: str) -> pd.Series:
    """Gross equal-weight basket of the top-N by dollar volume, re-formed each week."""
    idx = inp.index
    ret = np.nan_to_num(inp.close[1:] / inp.mark[:-1] - 1.0)  # close-to-close, row i -> i+1
    s = int(idx.searchsorted(pd.Timestamp(start)))
    e = int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    value = 1.0
    out = np.empty(e - s + 1)
    weights = np.zeros(len(inp.names))
    # Start from the latest formation before the period.
    j = s
    while j > 0 and not inp.weekly[j - 1]:
        j -= 1
    universe = top_universe(inp, max(j - 1, 0), top_n)
    weights[universe] = 1.0 / len(universe)
    for i in range(s, e + 1):
        if i > s:
            value *= 1.0 + float(np.dot(weights, ret[i - 1]))
        out[i - s] = value
        if inp.weekly[i]:
            universe = top_universe(inp, i, top_n)
            weights = np.zeros(len(inp.names))
            weights[universe] = 1.0 / len(universe)
    return pd.Series(out, index=idx[s : e + 1])


# --------------------------------------------------------------------------- metrics


def stats(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    returns = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0
    sharpe = returns.mean() / returns.std() * math.sqrt(TRADING_DAYS) if returns.std() > 0 else float("nan")
    yearly = equity.resample("YE").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0] - pd.Timedelta(days=1)])
    calendar = pd.concat([first, yearly]).pct_change().dropna()
    return {
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "max_dd": float((equity / equity.cummax() - 1.0).min()),
        "worst_year": float(calendar.min()),
        "calendar": {str(k.year): float(v) for k, v in calendar.items()},
    }
