from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbt.systems import PricePanel, TsmomConfig, TurtleConfig, run_tsmom, run_turtle
from quantbt.systems.tsmom import target_weights
from quantbt.systems.turtle import wilder_n


def _frame(closes: list[float], spread: float = 0.5, start: str = "2010-01-04") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c + spread, "low": c - spread, "close": c})


def _turtle(frames: dict[str, pd.DataFrame], **kwargs) -> object:
    groups = {s: (s, s) for s in frames}
    cfg = TurtleConfig(cash_symbol=None, cost_bps_per_side=0.0, **kwargs)
    return run_turtle(PricePanel.from_frames(frames), cfg, groups=groups)


def test_wilder_n_seed_and_smoothing() -> None:
    f = _frame([100.0] * 25, spread=1.0)
    n = wilder_n(f["high"], f["low"], f["close"], 20)
    assert np.isnan(n.iloc[18])
    assert n.iloc[19] == pytest.approx(2.0)
    assert n.iloc[24] == pytest.approx(2.0)


def test_system2_breakout_entry_stop_and_pyramid() -> None:
    # 60 flat days (N = 1.0), then a steady rise of 1 per day.
    closes = [100.0] * 60 + [100.0 + i for i in range(1, 11)]
    res = _turtle({"AAA": _frame(closes)}, system=2)
    lev = res.gross_leverage
    first_day = lev[lev > 0].index[0]
    assert first_day == pd.bdate_range("2010-01-04", periods=61)[-1]
    # Unit = 1% * 1,000,000 / N(=1) = 10,000 shares at the 55-day high 100.5.
    assert res.trades.empty  # still in the trend at the end
    # Day 1 (high 101.5): entry at the 55-day high 100.5, adds at 101.0 and 101.5; close 101.
    # Unit = 1% * 1,000,000 / N(=1) = 10,000 shares, so P&L = 10,000 * (0.5 + 0 - 0.5) = 0.
    assert res.equity.loc[first_day] == pytest.approx(1_000_000)
    # Capped at 4 units of about 10,000 shares.
    shares = res.gross_leverage * res.equity / pd.Series(closes, index=res.equity.index)
    assert shares.max() == pytest.approx(40_000, rel=0.01)  # 4th unit sized on the updated N


def test_system2_exits_at_nearer_of_channel_and_stop() -> None:
    closes = [100.0] * 60 + [101.0, 99.0, 97.0, 95.0, 93.0]
    res = _turtle({"AAA": _frame(closes)}, system=2, max_units_market=1)
    trade = res.trades.iloc[0]
    assert trade["direction"] == 1
    assert trade["entry_price"] == pytest.approx(100.5)
    # 20-day low 99.5 is above the 2N stop 98.5, so the channel exit fires first.
    assert trade["exit_reason"] == "exit"
    assert trade["exit_price"] == pytest.approx(99.5)


def test_stops_move_up_with_each_unit() -> None:
    closes = [100.0] * 60 + [101.0, 102.0, 103.0, 98.0]
    res = _turtle({"AAA": _frame(closes)}, system=2)
    trade = res.trades.iloc[0]
    assert trade["units"] == 4
    # Last add at 102.0 (entry 100.5 + 3 * 0.5N) -> stop 100.0, above the 20-day low 99.5.
    assert trade["exit_reason"] == "stop"
    assert trade["exit_price"] == pytest.approx(100.0)


def test_gap_through_entry_fills_at_open() -> None:
    f = _frame([100.0] * 60 + [110.0])
    f.iloc[-1, f.columns.get_loc("open")] = 108.0
    res = _turtle({"AAA": f}, system=2, max_units_market=1)
    # Long 10,000 at the 108 open, marked at 110.
    assert res.equity.iloc[-1] == pytest.approx(1_000_000 + 10_000 * 2.0)


def test_system1_skips_after_winning_breakout() -> None:
    # Breakout 1 wins (rise, then a 10-day-low exit above entry). Breakout 2 must be skipped.
    flat = [100.0] * 60
    up = [100.0 + i for i in range(1, 16)]  # rises to 115
    fall = [115.0 - 0.4 * i for i in range(1, 25)]  # drifts down, hits 10-day low exit well above 100.5
    base = fall[-1]
    flat2 = [base] * 25
    up2 = [base + i for i in range(1, 6)]
    res = _turtle({"AAA": _frame(flat + up + fall + flat2 + up2)}, system=1)
    assert len(res.trades) == 1
    assert res.trades.iloc[0]["pnl_before_costs"] > 0
    # No position after the second 20-day breakout (it was skipped, and no 55-day failsafe break).
    assert res.gross_leverage.iloc[-1] == 0.0


def test_system1_takes_breakout_after_losing_one() -> None:
    # Breakout at 100.5, then a touch of the 10-day low 99.5: a losing exit with no short breakout.
    flat = [100.0] * 60
    loser = [101.0, 100.0]
    flat2 = [100.0] * 25
    up2 = [100.0 + i for i in range(1, 6)]
    res = _turtle({"AAA": _frame(flat + loser + flat2 + up2)}, system=1)
    assert len(res.trades) == 1
    assert res.trades.iloc[0]["pnl_before_costs"] < 0
    assert res.gross_leverage.iloc[-1] > 0


def test_unit_limit_across_correlated_markets() -> None:
    closes = [100.0] * 60 + [100.0 + i for i in range(1, 11)]
    frames = {s: _frame(closes) for s in ("AAA", "BBB")}
    panel = PricePanel.from_frames(frames)
    groups = {"AAA": ("g", "g"), "BBB": ("g", "g")}
    res = run_turtle(panel, TurtleConfig(system=2, cash_symbol=None, cost_bps_per_side=0.0), groups=groups)
    # 6 units max across the closely correlated pair, each unit 10,000 shares.
    gross_shares = res.gross_leverage.iloc[-1] * res.equity.iloc[-1] / 110.0
    assert gross_shares == pytest.approx(60_000, rel=1e-6)


def test_tsmom_sign_size_and_next_open_execution() -> None:
    rng = np.random.default_rng(0)
    n = 400
    up = 100 * np.cumprod(1 + 0.001 + 0.01 * rng.standard_normal(n))
    down = 100 * np.cumprod(1 - 0.001 + 0.01 * rng.standard_normal(n))
    frames = {"SPY": _frame(list(up), spread=0.1), "TLT": _frame(list(down), spread=0.1)}
    panel = PricePanel.from_frames(frames)
    cfg = TsmomConfig(cash_symbol=None, cost_bps_per_side=0.0)
    w = target_weights(panel, ["SPY", "TLT"], cfg)
    last = w.iloc[-2]
    assert last["SPY"] > 0 > last["TLT"]
    # Each leg is 40% / sigma / 2 instruments; sigma about 16% annualised here.
    assert abs(last["SPY"]) == pytest.approx(0.4 / 0.16 / 2, rel=0.35)
    res = run_tsmom(panel, cfg, symbols=["SPY", "TLT"])
    first_trade = res.turnover[res.turnover > 0].index[0]
    first_signal = w[(w != 0).any(axis=1)].index[0]
    assert first_trade == panel.index[panel.index.get_loc(first_signal) + 1]


def test_system1_failsafe_55_day_entry_after_skip() -> None:
    flat = [100.0] * 60
    up = [100.0 + i for i in range(1, 16)]
    fall = [115.0 - 0.4 * i for i in range(1, 25)]
    base = fall[-1]
    flat2 = [base] * 25
    up2 = [base + i for i in range(1, 16)]  # climbs past the prior 55-day high
    res = _turtle({"AAA": _frame(flat + up + fall + flat2 + up2)}, system=1)
    lev = res.gross_leverage
    skipped_until = lev.index[60 + 15 + 24 + 25 + 4]
    assert lev.loc[skipped_until] == 0.0
    assert lev.iloc[-1] > 0
