import numpy as np
import pandas as pd

from quantbt.weights_sim import simulate_target_weights


def _frame(values, idx, col="X"):
    return pd.DataFrame({col: values}, index=idx)


def test_fills_at_next_open_and_charges_costs():
    idx = pd.bdate_range("2020-01-01", periods=4)
    o = _frame([10.0, 11.0, 12.0, 13.0], idx)
    c = _frame([10.5, 11.5, 12.5, 13.5], idx)
    targets = _frame([1.0], idx[:1])  # signal at day 0 close -> buy day 1 open at 11
    eq, gross, _ = simulate_target_weights(o, o, o, c, targets, pd.Series(0.0, index=idx), cost_bps=10.0)
    assert eq.iloc[0] == 1.0
    # day1 close: position worth 1.0 * 11.5/11, minus 10 bps cost on 1.0 traded
    assert np.isclose(eq.iloc[1], 11.5 / 11.0 - 0.001)
    assert np.isclose(eq.iloc[3], (13.5 / 11.0) - 0.001)
    assert np.isclose(gross.iloc[3], (13.5 / 11.0) / eq.iloc[3])


def test_cash_earns_rate_and_short_pays_fee():
    idx = pd.DatetimeIndex(["2020-01-01", "2021-01-01"])
    p = _frame([10.0, 10.0], idx)
    eq, _, _ = simulate_target_weights(p, p, p, p, _frame([0.0], idx[:1]), pd.Series(0.05, index=idx), cost_bps=0.0)
    assert np.isclose(eq.iloc[1], 1.0 + 0.05 * 366 / 365)


def test_delisted_position_sold_at_last_close():
    idx = pd.bdate_range("2020-01-01", periods=4)
    o = _frame([10.0, 10.0, np.nan, np.nan], idx)
    c = _frame([10.0, 8.0, np.nan, np.nan], idx)
    eq, gross, _ = simulate_target_weights(o, o, o, c, _frame([1.0], idx[:1]), pd.Series(0.0, index=idx), cost_bps=0.0)
    assert np.isclose(eq.iloc[-1], 0.8)
    assert gross.iloc[-1] == 0.0


def test_skid_fill():
    idx = pd.bdate_range("2020-01-01", periods=3)
    o = _frame([10.0, 10.0, 10.0], idx)
    h = _frame([12.0, 12.0, 12.0], idx)
    c = _frame([10.0, 10.0, 10.0], idx)
    eq, _, _ = simulate_target_weights(o, h, o, c, _frame([1.0], idx[:1]), pd.Series(0.0, index=idx), skid=0.5)
    # bought at 11 instead of 10: lose 10% of the traded value
    assert np.isclose(eq.iloc[1], 0.9)
