"""
Best-strategy search: vol-scaled momentum, panic-only reversal and a trend sleeve.

Rules, variants and the selection rule are fixed in reports/best_strategy/PREREGISTRATION.md.
Everything here follows the project's timing convention: decide on a close, trade at the next
open. One simulator runs every stock variant, so they share fills, costs and financing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantbt.research.megacap_reversal import TRADING_DAYS, VolumePanel

SPY = "SPY"


# --------------------------------------------------------------------------- inputs


@dataclass
class Data:
    """Arrays shared by all variants (rows = trading days, columns = tickers)."""

    index: pd.DatetimeIndex
    names: pd.Index
    open: np.ndarray
    close: np.ndarray
    mark: np.ndarray  # forward-filled close
    ret: np.ndarray  # close-to-close return, 0 where unknown
    mom: np.ndarray  # 12-1 month return
    hi52: np.ndarray  # close / 252-day highest close
    rank_ok: np.ndarray  # member with a price today
    rel5: np.ndarray  # 5-day return minus SPY's
    dv: np.ndarray  # 63-day average dollar volume
    mega_ok: np.ndarray  # member, 63 days of history, finite rel5 and dv
    big: np.ndarray  # one of the 50 largest members by dollar volume (or SPY)
    last_row: np.ndarray
    spy_col: int
    weekly: np.ndarray  # last trading day of the week
    month_day: np.ndarray  # 1 = first trading day of the month
    cook: np.ndarray
    vix_high: np.ndarray
    spy_close: np.ndarray
    spy_sma200: np.ndarray
    tbill: np.ndarray


def build_data(panel: VolumePanel, tbill: pd.Series, cook: pd.Series, vix: pd.Series,
               etfs: tuple[str, ...] = ()) -> Data:
    close = panel.close
    index = close.index
    if SPY not in close.columns:
        raise ValueError("SPY must be in the price panel.")
    spy = close[SPY].ffill()
    member = panel.member.copy()
    member[SPY] = False

    filled = close.ffill(limit=5)
    mom = filled.shift(21) / filled.shift(252) - 1.0
    hi52 = close / close.rolling(252, min_periods=200).max()
    rank_ok = member & close.notna()

    rel5 = close.pct_change(5, fill_method=None).sub(spy.pct_change(5), axis=0)
    dv = panel.dollar_volume.rolling(63, min_periods=63).mean()
    mega_ok = member & (close.notna().cumsum() >= 63) & close.notna() & rel5.notna() & dv.notna()
    dv_rank = dv.where(mega_ok).rank(axis=1, ascending=False, method="first")
    big = dv_rank <= 50
    for etf in (SPY, *etfs):
        big[etf] = True
        member[etf] = False

    last_valid = close.apply(lambda s: s.last_valid_index())
    last_row = index.get_indexer(pd.DatetimeIndex(last_valid.values))
    week = index.to_period("W-FRI")
    weekly = np.r_[week[1:] != week[:-1], True]
    month = index.to_period("M")
    month_day = pd.Series(1, index=index).groupby(month).cumsum().to_numpy()

    vix = vix.reindex(index.union(vix.index)).ffill().reindex(index)
    vix_high = vix > vix.rolling(252, min_periods=252).quantile(0.8).shift(1)

    mark = close.ffill()
    ret = (close / mark.shift(1) - 1.0).fillna(0.0)
    return Data(
        index=index,
        names=close.columns,
        open=panel.open.to_numpy(),
        close=close.to_numpy(),
        mark=mark.to_numpy(),
        ret=ret.to_numpy(),
        mom=mom.to_numpy(),
        hi52=hi52.to_numpy(),
        rank_ok=rank_ok.to_numpy(),
        rel5=rel5.to_numpy(),
        dv=dv.to_numpy(),
        mega_ok=mega_ok.to_numpy(),
        big=big.to_numpy(),
        last_row=last_row,
        spy_col=int(close.columns.get_loc(SPY)),
        weekly=weekly,
        month_day=month_day,
        cook=cook.reindex(index).fillna(False).astype(bool).to_numpy(),
        vix_high=vix_high.fillna(False).to_numpy(),
        spy_close=spy.to_numpy(),
        spy_sma200=spy.rolling(200).mean().to_numpy(),
        tbill=tbill.reindex(index).fillna(0.0).to_numpy(),
    )


# --------------------------------------------------------------------------- config


@dataclass
class Costs:
    fee_frac: float  # per order, as a fraction of equity
    spread_big_bps: float  # half-spread: SPY and the 50 largest members
    spread_small_bps: float  # half-spread: other members
    borrow_spread: float = 0.03  # over T-bills, per year


SCALABLE = Costs(fee_frac=1.99 / 25_000.0, spread_big_bps=6.0, spread_small_bps=25.0)
US_BROKER = Costs(fee_frac=0.4 / 10_000.0, spread_big_bps=3.0, spread_small_bps=3.0)
ZERO_COST = Costs(fee_frac=0.0, spread_big_bps=0.0, spread_small_bps=0.0)


@dataclass
class Config:
    core: str = "mom"  # "mom" or "spy"
    rank: str = "mom"  # "mom" (12-1 return) or "hi52" (closeness to the 52-week high)
    top_n: int = 10
    hold_rank: int = 30
    risk: str = "none"  # "none", "vol" or "band200"
    vol_target: float = 0.20
    vol_window: int = 63
    max_lev: float = 1.0
    index: str = SPY  # the index ETF held by core "spy" and by panic mode "to_index"
    # "none"; "lev_spy" (core "spy": lever the index); "reversal" (core "spy": losers replace the index);
    # "fill" (core "mom": idle capital into losers); "to_index" (core "mom": the levered index replaces the book)
    panic: str = "none"
    panic_stop: float | None = None  # index loss since the panic began that ends the extra leverage
    panic_delay: int = 0  # extra days to wait before acting on the panic state
    trigger: str = "cook"  # "cook" or "cook_or_vix"
    panic_lev: float = 1.5
    rebalance_day: int = 1  # trading day of the month
    band: float = 0.10  # relative distance from target before a position is adjusted
    exposure_step: float = 0.10
    sma_buffer: float = 0.03
    n_losers: int = 5
    initial: float = 25_000.0


@dataclass
class Result:
    equity: pd.Series
    exposure: pd.Series
    orders: int
    fees: float
    spread_cost: float
    trades: list[tuple]  # (date, ticker, signed notional / equity)
    books: list[tuple]  # (date, [tickers]) at each momentum rebalance


# --------------------------------------------------------------------------- selection


def momentum_book(d: Data, i: int, held: list[int], cfg: Config) -> list[int]:
    """Keep held names still ranked in the top `hold_rank`; refill to `top_n` from the best others."""
    score = d.mom[i] if cfg.rank == "mom" else d.hi52[i]
    cols = np.flatnonzero(d.rank_ok[i] & np.isfinite(score))
    if len(cols) < cfg.hold_rank:
        return held
    order = cols[np.argsort(-score[cols], kind="stable")]
    top_hold = set(order[: cfg.hold_rank].tolist())
    book = [c for c in held if c in top_hold][: cfg.top_n]
    for c in order:
        if len(book) >= cfg.top_n:
            break
        if c not in book:
            book.append(int(c))
    return book


def panic_losers(d: Data, i: int, n: int) -> list[int]:
    """The `n` lowest 5-day relative returns among the 50 largest members."""
    cols = np.flatnonzero(d.mega_ok[i])
    if len(cols) < 50:
        return []
    big = cols[np.argsort(-d.dv[i, cols], kind="stable")[:50]]
    return big[np.argsort(d.rel5[i, big], kind="stable")[:n]].tolist()


# --------------------------------------------------------------------------- simulator


def run(d: Data, cfg: Config, costs: Costs, start: str, end: str | None = None) -> Result:
    idx = d.index
    s = int(idx.searchsorted(pd.Timestamp(start)))
    e = len(idx) - 1 if end is None else int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    width = len(d.names)
    borrow = costs.borrow_spread / TRADING_DAYS

    shares = np.zeros(width)
    cash = cfg.initial
    pending: np.ndarray | None = None
    book: list[int] = []
    losers: list[int] = []
    book_returns: list[float] = []
    exposure_now = 1.0
    band_in = True
    panic_prev = False
    stopped = False
    stop_ref = float("nan")
    index_col = int(d.names.get_loc(cfg.index))
    orders = 0
    fees = 0.0
    spread_cost = 0.0
    trades: list[tuple] = []
    books: list[tuple] = []
    equity = np.full(e - s + 1, np.nan)
    exposure = np.zeros(e - s + 1)

    for i in range(s, e + 1):
        tb = d.tbill[i]
        cash += cash * tb if cash >= 0 else cash * (tb + borrow)

        # ---- open: trade toward the weights decided on the previous close
        if pending is not None:
            px = d.open[i]
            tradable = np.isfinite(px)
            mark = np.where(tradable, px, d.mark[i - 1])
            cur = np.nan_to_num(shares * mark)
            value = cash + float(cur.sum())
            target = pending * value
            sell_all = (target == 0) & (shares != 0)
            buy_new = (target > 0) & (shares == 0)
            adjust = (target > 0) & (shares != 0) & (np.abs(target - cur) > cfg.band * target)
            fee = costs.fee_frac * value
            for col in np.flatnonzero(tradable & (sell_all | buy_new | adjust)):
                delta = target[col] - cur[col]
                half = (costs.spread_big_bps if d.big[i - 1, col] else costs.spread_small_bps) / 10_000.0
                shares[col] = 0.0 if sell_all[col] else shares[col] + delta / px[col]
                cash -= delta + fee + half * abs(delta)
                orders += 1
                fees += fee
                spread_cost += half * abs(delta)
                trades.append((idx[i], d.names[col], delta / value))
            pending = None

        # ---- close: names leaving the data are sold at their last close
        for col in np.flatnonzero(shares != 0):
            if d.last_row[col] == i and i < len(idx) - 1:
                proceeds = shares[col] * d.close[i, col]
                half = (costs.spread_big_bps if d.big[i, col] else costs.spread_small_bps) / 10_000.0
                fee = costs.fee_frac * (cash + float(np.nansum(shares * d.mark[i])))
                cash += proceeds - fee - half * abs(proceeds)
                orders += 1
                fees += fee
                spread_cost += half * abs(proceeds)
                shares[col] = 0.0
                trades.append((idx[i], d.names[col], float("nan")))
        held_val = float(np.nansum(shares * d.mark[i]))
        value = cash + held_val
        equity[i - s] = value
        exposure[i - s] = held_val / value if value > 0 else 0.0
        if value <= 0 or i == e:
            break
        if cfg.core == "mom" and book and i > s:
            book_returns.append(float(np.mean(d.ret[i, book])))

        # ---- decisions on this close
        first = i == s
        monthly = d.month_day[i] == cfg.rebalance_day
        weekly = bool(d.weekly[i])
        j = i - cfg.panic_delay
        panic_on = bool(d.cook[j]) or (cfg.trigger == "cook_or_vix" and bool(d.vix_high[j]))
        if cfg.panic == "none":
            panic_on = False
        act = first
        if cfg.panic_stop is not None:
            if panic_on and not panic_prev:
                stop_ref, stopped = d.mark[i, index_col], False
            elif panic_on and not stopped and d.mark[i, index_col] < stop_ref * (1.0 - cfg.panic_stop):
                stopped, act = True, True
            elif not panic_on:
                stopped = False
        lev_on = panic_on and not stopped

        if cfg.core == "mom" and (monthly or first or not book):
            new_book = momentum_book(d, i, [c for c in book if d.last_row[c] > i], cfg)
            if monthly or new_book != book:
                act = True
            if new_book != book or first:
                books.append((idx[i], [d.names[c] for c in new_book]))
            book = new_book

        want = exposure_now
        if cfg.risk == "vol" and (weekly or monthly or first):
            if len(book_returns) >= cfg.vol_window:
                vol = float(np.std(book_returns[-cfg.vol_window:], ddof=1)) * math.sqrt(TRADING_DAYS)
                want = min(cfg.max_lev, cfg.vol_target / vol) if vol > 0 else cfg.max_lev
            else:
                want = min(cfg.max_lev, 1.0)
        elif cfg.risk == "band200" and (weekly or first):
            sma = d.spy_sma200[i]
            if first:
                band_in = bool(d.spy_close[i] > sma)
            elif band_in and d.spy_close[i] < sma * (1.0 - cfg.sma_buffer):
                band_in = False
            elif not band_in and d.spy_close[i] > sma * (1.0 + cfg.sma_buffer):
                band_in = True
            want = 1.0 if band_in else 0.0
        elif cfg.risk == "none":
            want = 1.0
        if first or abs(want - exposure_now) > cfg.exposure_step or (want == 0.0) != (exposure_now == 0.0):
            if want != exposure_now:
                act = True
            exposure_now = want

        if panic_on != panic_prev:
            act = True
        if cfg.panic in ("reversal", "fill"):
            if panic_on and (not panic_prev or weekly or not losers):
                losers = panic_losers(d, i, cfg.n_losers)
                act = True
            if not panic_on:
                losers = []
        panic_prev = panic_on

        if act:
            w = np.zeros(width)
            if cfg.core == "spy":
                if cfg.panic == "reversal" and panic_on and losers:
                    w[losers] = 1.0 / len(losers)
                else:
                    w[index_col] = cfg.panic_lev if (cfg.panic == "lev_spy" and lev_on) else 1.0
            elif cfg.panic == "to_index" and panic_on:
                w[index_col] = cfg.panic_lev if lev_on else 1.0
            else:
                if book:
                    w[book] = exposure_now / cfg.top_n
                spare = 1.0 - exposure_now
                if cfg.panic == "fill" and panic_on and losers and spare > cfg.exposure_step:
                    w[losers] += spare / len(losers)
            pending = w

    dates = idx[s : e + 1]
    return Result(
        equity=pd.Series(equity, index=dates, name="equity").dropna(),
        exposure=pd.Series(exposure, index=dates, name="exposure"),
        orders=orders,
        fees=fees,
        spread_cost=spread_cost,
        trades=trades,
        books=books,
    )


# --------------------------------------------------------------------------- benchmarks and mixes


def equal_weight_members(d: Data, start: str) -> pd.Series:
    """Gross, daily-rebalanced equal-weight basket of every member with a price."""
    held = np.vstack([np.zeros(d.rank_ok.shape[1], dtype=bool), d.rank_ok[:-1]])
    n = held.sum(axis=1)
    r = np.where(n > 0, (d.ret * held).sum(axis=1) / np.maximum(n, 1), 0.0)
    eq = pd.Series(np.cumprod(1.0 + r), index=d.index)
    return eq[eq.index >= pd.Timestamp(start)]


def mix_with_vol_target(a: pd.Series, b: pd.Series, tbill: pd.Series, weight_a: float = 0.7,
                        vol_target: float = 0.20, window: int = 63, max_lev: float = 2.0,
                        step: float = 0.10, cost_bps: float = 10.0, borrow_spread: float = 0.03) -> pd.Series:
    """
    Monthly-rebalanced mix of two equity curves, scaled weekly to a volatility target.
    The scale decided on a close applies from the next day; idle capital earns T-bills and
    borrowed capital pays T-bills plus a spread. An approximation built from daily returns.
    """
    idx = a.index.intersection(b.index)
    ra, rb = a.reindex(idx).pct_change().fillna(0.0), b.reindex(idx).pct_change().fillna(0.0)
    tb = tbill.reindex(idx).fillna(0.0).to_numpy()
    month = idx.to_period("M")
    week = idx.to_period("W-FRI")
    va, vb = weight_a, 1.0 - weight_a
    k = 1.0
    value = 1.0
    out = np.empty(len(idx))
    mix_returns: list[float] = []
    for t in range(len(idx)):
        if t > 0 and month[t] != month[t - 1]:
            total = va + vb
            va, vb = weight_a * total, (1.0 - weight_a) * total
        before = va + vb
        va *= 1.0 + ra.iloc[t]
        vb *= 1.0 + rb.iloc[t]
        m = (va + vb) / before - 1.0
        mix_returns.append(m)
        finance = (1.0 - k) * tb[t] if k <= 1.0 else -(k - 1.0) * (tb[t] + borrow_spread / TRADING_DAYS)
        value *= 1.0 + k * m + finance
        out[t] = value
        last_of_week = t == len(idx) - 1 or week[t + 1] != week[t]
        if last_of_week and len(mix_returns) >= window:
            vol = float(np.std(mix_returns[-window:], ddof=1)) * math.sqrt(TRADING_DAYS)
            want = min(max_lev, vol_target / vol) if vol > 0 else max_lev
            if abs(want - k) > step:
                value *= 1.0 - abs(want - k) * cost_bps / 10_000.0
                k = want
    return pd.Series(out, index=idx)


def trend_weights(close: pd.DataFrame, rf: pd.Series, classes: dict[str, str], target_vol: float = 0.40,
                  com: float = 60.0, min_days: int = 60, annualization: int = 261,
                  lookbacks: tuple[int, ...] = (1, 3, 12)) -> pd.DataFrame:
    """
    Month-end weights for the long-or-flat trend sleeve: the average sign of the 1-, 3- and
    12-month excess returns (negatives set to zero), sized 40% / volatility as in
    `quantbt.systems.tsmom`, with each asset class given an equal share of the book.
    """
    excess = close.pct_change(fill_method=None).sub(rf, axis=0)
    sigma = excess.ewm(com=com, min_periods=min_days, ignore_na=True).std() * np.sqrt(annualization)
    growth = (1.0 + excess.fillna(0.0)).cumprod().where(close.notna())
    month_end = close.groupby(close.index.to_period("M")).tail(1).index
    g = growth.loc[month_end].ffill()
    signs = [np.sign(g / g.shift(k) - 1.0) for k in lookbacks]
    signal = (sum(signs) / len(signs)).where(signs[-1].notna()).clip(lower=0.0)
    raw = signal * target_vol / sigma.loc[month_end]
    out = pd.DataFrame(0.0, index=month_end, columns=close.columns)
    groups = sorted(set(classes.values()))
    for group in groups:
        cols = [c for c in close.columns if classes.get(c) == group]
        count = raw[cols].notna().sum(axis=1).replace(0, np.nan)
        out[cols] = raw[cols].div(count, axis=0).fillna(0.0) / len(groups)
    return out
