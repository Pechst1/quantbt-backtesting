from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantbt.systems import PricePanel, TsmomConfig, TurtleConfig, run_tsmom, run_turtle
from quantbt.systems.overlay import NativeVolEstimator, VolTarget, capped_scale, financing_rate


def _random_walk(seed: int, n: int, drift: float, vol: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-04", periods=n)
    c = pd.Series(100 * np.cumprod(1 + drift + vol * rng.standard_normal(n)), index=idx)
    o = c.shift(1).fillna(c.iloc[0])
    hi = pd.concat([o, c], axis=1).max(axis=1) * (1 + vol / 2)
    lo = pd.concat([o, c], axis=1).min(axis=1) * (1 - vol / 2)
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c})


def _panel(n: int = 1500) -> PricePanel:
    return PricePanel.from_frames(
        {
            "SPY": _random_walk(1, n, 0.0005, 0.010),
            "TLT": _random_walk(2, n, -0.0003, 0.006),
            "FXE": _random_walk(3, n, 0.0002, 0.004),
            "GLD": _random_walk(4, n, 0.0004, 0.009),
        }
    )


def test_estimator_warms_up_then_targets() -> None:
    est = NativeVolEstimator(VolTarget(target_vol=0.2, vol_min_days=3))
    for _ in range(2):
        est.update(0.01)
    assert est.scale() == 0.0
    est.update(0.01)
    assert est.vol == pytest.approx(0.01 * math.sqrt(261))
    assert est.scale() == pytest.approx(0.2 / (0.01 * math.sqrt(261)))


def test_capped_scale_and_financing() -> None:
    assert capped_scale(5.0, native_gross=200.0, equity=100.0, max_gross=3.0) == pytest.approx(1.5)
    assert capped_scale(1.0, native_gross=200.0, equity=100.0, max_gross=3.0) == 1.0
    assert financing_rate(100.0, 0.0001, 50.0) == 0.0001
    assert financing_rate(-100.0, 0.0001, 50.0) == pytest.approx(0.0001 + 0.005 / 252)


def test_tsmom_overlay_hits_target_vol_within_cap() -> None:
    panel = _panel()
    cfg = TsmomConfig(cash_symbol=None, cost_bps_per_side=0.0)
    res = run_tsmom(panel, cfg, symbols=panel.symbols, vol_target=VolTarget(target_vol=0.10, max_gross=3.0))
    live = res.equity[res.scale > 0].iloc[300:]
    realized = live.pct_change().dropna().std() * math.sqrt(261)
    assert realized == pytest.approx(0.10, rel=0.3)
    assert res.gross_leverage.max() <= 3.0 * 1.05


def test_turtle_overlay_respects_gross_cap_and_warm_up() -> None:
    panel = _panel()
    groups = {s: (s, s) for s in panel.symbols}
    cfg = TurtleConfig(system=2, cash_symbol=None, cost_bps_per_side=0.0)
    native = run_turtle(panel, cfg, groups=groups)
    assert native.gross_leverage.max() > 3.0  # published sizing runs far above the cap here
    vt = VolTarget(target_vol=0.15, max_gross=2.0, vol_min_days=60)
    res = run_turtle(panel, cfg, groups=groups, vol_target=vt)
    # Nothing is held until the native-book vol estimate has 60 returns.
    assert (res.gross_leverage.iloc[:60] == 0.0).all()
    assert res.gross_leverage.max() <= 2.0 * 1.05  # cap at fills; closes can drift slightly past it
    assert res.gross_leverage.iloc[100:].max() > 0.0
    # Same trades as the native run: the overlay sizes positions, it does not change signals.
    assert len(res.trades) == len(native.trades)
