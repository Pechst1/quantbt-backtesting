import numpy as np
import pandas as pd
import pytest

from quantbt.research.wizards_new import (
    MeanReversionConfig,
    Panel,
    StatArbConfig,
    period_stats,
    run_mean_reversion,
    run_stat_arb,
    run_vrp,
    wilder_rsi,
)


def _panel(closes: dict[str, list[float]], opens: dict[str, list[float]] | None = None) -> Panel:
    index = pd.bdate_range("2020-01-01", periods=len(next(iter(closes.values()))))
    close = pd.DataFrame(closes, index=index, dtype=float)
    open_ = pd.DataFrame(opens, index=index, dtype=float) if opens else close.copy()
    return Panel(open=open_, close=close, member=pd.DataFrame(True, index=index, columns=close.columns))


def test_wilder_rsi_extremes():
    up = pd.Series(np.arange(1.0, 20.0))
    assert wilder_rsi(up, 2).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(20.0, 1.0, -1.0))
    assert wilder_rsi(down, 2).iloc[-1] == pytest.approx(0.0)


def test_mean_reversion_enters_next_open_and_exits_after_sma5_close():
    # 220 rising days, two sharp down days (RSI2 -> ~0), then a bounce.
    base = list(np.linspace(50, 100, 220))
    closes = base + [95.0, 90.0, 99.0, 100.0, 101.0, 102.0]
    opens = list(closes)
    opens[222] = 91.0  # open after the signal day
    opens[224] = 99.5  # open after the bounce close
    panel = _panel({"AAA": closes}, {"AAA": opens})
    tb = pd.Series(0.0, index=panel.close.index)
    cfg = MeanReversionConfig(slots=1, cost_bps=0.0)
    equity, trades = run_mean_reversion(panel, cfg, tb)
    assert len(trades) == 1
    trade = trades.iloc[0]
    # One sharp down day already drives RSI(2) below 5: signal on day 220, buy at the 221 open.
    assert trade["entry"] == panel.close.index[221]
    # Day 222 closes at 99, above its SMA5 (~96.9): sell at the 223 open.
    assert trade["exit"] == panel.close.index[223]
    assert trade["ret"] == pytest.approx(opens[223] / opens[221] - 1.0)


def test_parker_time_stop_sells_after_five_days():
    base = list(np.linspace(50, 100, 220))
    closes = base + [95.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0, 83.0]
    panel = _panel({"AAA": closes})
    tb = pd.Series(0.0, index=panel.close.index)
    cfg = MeanReversionConfig(slots=1, cost_bps=0.0, exit_rule="parker")
    _, trades = run_mean_reversion(panel, cfg, tb)
    first = trades.iloc[0]
    # signal on day 220, entry open 221, five lower closes 221..225, time-stop exit at open 226
    assert first["entry"] == panel.close.index[221]
    assert first["exit"] == panel.close.index[226]


def test_vrp_signal_earns_from_two_days_later():
    index = pd.bdate_range("2020-01-01", periods=6)
    short_vol = pd.Series([100, 110, 121, 133.1, 146.41, 161.051], index=index, dtype=float)  # +10%/day
    long_vol = pd.Series(100.0, index=index)
    signal = pd.Series([True, False, False, False, False, False], index=index)
    eq = run_vrp(signal, short_vol, long_vol, pd.Series(0.0, index=index), off="cash", cost_bps=0.0)
    daily = eq.pct_change().fillna(0.0)
    # signal on day 0 -> bought at close of day 1 -> earns day 2's return only
    assert daily.iloc[2] == pytest.approx(0.10)
    assert daily.drop(daily.index[2]).abs().max() == pytest.approx(0.0)


def test_stat_arb_long_short_is_market_neutral_on_a_common_move():
    n = 140
    index = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(0)
    closes = {f"S{i:02d}": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)) for i in range(60)}
    panel = _panel(closes)
    common = 1.05 ** np.arange(n)  # every stock gains the same 5% a day on top
    panel = Panel(open=panel.open.mul(common, axis=0), close=panel.close.mul(common, axis=0), member=panel.member)
    spy = panel.close.mean(axis=1)
    eq = run_stat_arb(panel, StatArbConfig(cost_bps=0.0, borrow_bps_yr=0.0), spy, pd.Series(0.0, index=index))
    # a 5%/day common factor would explode any net-long book; neutral book stays modest
    assert eq.iloc[-1] / eq.iloc[0] < 3.0


def test_period_stats_counts_first_day():
    index = pd.bdate_range("2020-01-01", periods=4)
    eq = pd.Series([100.0, 110.0, 121.0, 133.1], index=index)
    stats = period_stats(eq, str(index[1].date()), str(index[-1].date()))
    assert stats["max_dd"] == 0.0
    assert stats["worst_day"] == pytest.approx(0.10)


def test_stat_arb_cohorts_cut_turnover():
    n = 260
    index = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(1)
    closes = {f"S{i:02d}": 100 * np.cumprod(1 + rng.normal(0, 0.02, n)) for i in range(80)}
    panel = _panel(closes)
    spy = panel.close.mean(axis=1)
    tb = pd.Series(0.0, index=index)
    gross = run_stat_arb(panel, StatArbConfig(cost_bps=0.0, borrow_bps_yr=0.0), spy, tb)
    for k in (1, 4):
        free = run_stat_arb(panel, StatArbConfig(cost_bps=0.0, borrow_bps_yr=0.0, cohorts=k), spy, tb)
        paid = run_stat_arb(panel, StatArbConfig(cost_bps=100.0, borrow_bps_yr=0.0, cohorts=k), spy, tb)
        drag = np.log(free.iloc[-1] / paid.iloc[-1])
        if k == 1:
            assert free.iloc[-1] == pytest.approx(gross.iloc[-1])
            drag_one = drag
    assert drag < 0.6 * drag_one  # four overlapping cohorts trade much less than one
