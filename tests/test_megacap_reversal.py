import numpy as np
import pandas as pd
import pytest

from quantbt.research.megacap_reversal import (
    MegacapConfig,
    VolumePanel,
    build_inputs,
    cook_breadth_state,
    run_megacap,
)


def _panel(n_names=20, n_days=400, seed=0):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2010-01-01", periods=n_days)
    rets = rng.normal(0.0003, 0.015, size=(n_days, n_names))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=index,
                         columns=[f"S{i:02d}" for i in range(n_names)])
    dv = pd.DataFrame(np.tile(np.arange(n_names, 0, -1) * 1e6, (n_days, 1)), index=index, columns=close.columns)
    member = pd.DataFrame(True, index=index, columns=close.columns)
    return VolumePanel(open=close.copy(), close=close, dollar_volume=dv, member=member)


def _inputs(panel, trend_on=True):
    spy = panel.close.mean(axis=1)
    tb = pd.Series(0.0, index=panel.close.index)
    cook = pd.Series(False, index=panel.close.index)
    inp = build_inputs(panel, spy, tb, cook)
    for k in inp.trend:
        inp.trend[k] = np.full(len(inp.index), trend_on)
    return inp


def test_zero_cost_always_in_stays_invested_and_orders_counted():
    panel = _panel()
    inp = _inputs(panel)
    cfg = MegacapConfig(top_n=10, trend="none", cook=False, fee=0.0, spread_bps=0.0, account=10_000)
    res = run_megacap(inp, cfg, "2010-06-01", "2011-06-01")
    assert res.exposure.iloc[5:].min() > 0.95
    assert res.orders > 0 and res.fees == 0.0


def test_fee_reduces_equity_by_fees_paid():
    panel = _panel()
    inp = _inputs(panel)
    free = run_megacap(inp, MegacapConfig(top_n=10, trend="none", cook=False, fee=0.0, spread_bps=0.0), "2010-06-01", "2011-06-01")
    paid = run_megacap(inp, MegacapConfig(top_n=10, trend="none", cook=False, fee=2.0, spread_bps=0.0), "2010-06-01", "2011-06-01")
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    assert paid.fees == pytest.approx(2.0 * paid.orders)


def test_trend_off_means_cash():
    panel = _panel()
    inp = _inputs(panel, trend_on=False)
    res = run_megacap(inp, MegacapConfig(top_n=10, trend="200d", cook=False), "2010-06-01", "2011-06-01")
    assert res.orders == 0
    assert res.equity.iloc[-1] == pytest.approx(25_000.0)


def test_cook_state_turns_on_after_a_selloff():
    index = pd.bdate_range("2000-01-01", periods=700)
    rng = np.random.default_rng(1)
    rets = rng.normal(0, 0.01, size=(700, 60))
    rets[650:665] = -0.02  # broad, persistent selling
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=index)
    member = pd.DataFrame(True, index=index, columns=close.columns)
    state = cook_breadth_state(close, member)
    buy = state["buy"]
    assert not buy.iloc[:504].any()  # needs 504 values of history first
    window = buy.loc[index[655]:index[670]]
    assert window.any()
