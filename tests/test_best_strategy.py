from __future__ import annotations

import numpy as np
import pandas as pd

from quantbt.research import best_strategy as bs
from quantbt.research.megacap_reversal import VolumePanel


def make_data(days: int = 400, cook_on: slice | None = None) -> bs.Data:
    index = pd.bdate_range("2020-01-01", periods=days)
    rng = np.random.default_rng(0)
    names = ["SPY"] + [f"S{k:02d}" for k in range(60)]
    drift = np.linspace(-0.001, 0.002, len(names))
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal((days, len(names))), axis=0)),
                         index=index, columns=names)
    open_ = close.shift(1).fillna(100.0) * 1.001
    panel = VolumePanel(open=open_, close=close,
                        dollar_volume=pd.DataFrame(1e6, index=index, columns=names) * np.arange(1, len(names) + 1),
                        member=pd.DataFrame(True, index=index, columns=names))
    cook = pd.Series(False, index=index)
    if cook_on is not None:
        cook.iloc[cook_on] = True
    return bs.build_data(panel, pd.Series(0.0, index=index), cook, pd.Series(15.0, index=index))


def test_decision_on_close_fills_at_next_open_with_costs() -> None:
    d = make_data()
    start = str(d.index[300].date())
    costs = bs.Costs(fee_frac=0.001, spread_big_bps=10.0, spread_small_bps=10.0)
    res = bs.run(d, bs.Config(core="spy"), costs, start)
    # Day 0: cash only. Day 1: all-in SPY at the open, paying fee and half-spread.
    assert res.equity.iloc[0] == 25_000.0
    assert res.trades[0][0] == d.index[301] and res.trades[0][1] == "SPY"
    spy = d.spy_col
    expected = 25_000.0 * d.close[301, spy] / d.open[301, spy] - 25_000.0 * 0.002
    assert abs(res.equity.iloc[1] - expected) < 1e-6
    free = bs.run(d, bs.Config(core="spy"), bs.ZERO_COST, start)
    ratio = free.equity.iloc[-1] / free.equity.iloc[1]
    assert abs(ratio - d.close[-1, spy] / d.close[301, spy]) < 1e-9


def test_panic_leverage_switches_one_bar_after_the_signal() -> None:
    d = make_data(cook_on=slice(320, 330))
    res = bs.run(d, bs.Config(core="spy", panic="lev_spy", panic_lev=1.5), bs.ZERO_COST, str(d.index[300].date()))
    exposure = res.exposure.to_numpy()
    assert abs(exposure[20] - 1.0) < 0.02  # signal first seen on close 320: still 1x that day
    assert abs(exposure[21] - 1.5) < 0.02  # levered from the next open
    assert abs(exposure[30] - 1.5) < 0.05  # signal off on close 330, still levered that day
    assert abs(exposure[31] - 1.0) < 0.02


def test_momentum_book_keeps_holdings_inside_the_hold_rank() -> None:
    d = make_data()
    i = 350
    cfg = bs.Config(top_n=10, hold_rank=30)
    order = np.argsort(-np.where(d.rank_ok[i], d.mom[i], -np.inf))
    keeper, dropped = int(order[25]), int(order[40])
    book = bs.momentum_book(d, i, [keeper, dropped], cfg)
    assert keeper in book and dropped not in book and len(book) == 10
    assert set(book) - {keeper} == set(order[:9].tolist())


def test_vol_scaling_never_exceeds_the_cap() -> None:
    d = make_data()
    res = bs.run(d, bs.Config(risk="vol", vol_target=0.50, max_lev=1.0), bs.ZERO_COST, str(d.index[260].date()))
    assert res.exposure.max() < 1.05
    low = bs.run(d, bs.Config(risk="vol", vol_target=0.02, max_lev=1.0), bs.ZERO_COST, str(d.index[260].date()))
    assert low.exposure.iloc[-1] < 0.6
