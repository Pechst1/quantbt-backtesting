"""
Audit and robustness checks for reports/best_strategy (run after run_best_strategy.py).
None of these can change the pre-registered outcome; they describe it. Writes audit.json.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_best_strategy import (
    OUT,
    RUN_START,
    VARIANTS,
    load_inputs,
    window_stats,
)

from quantbt.research import best_strategy as bs

WINS = ["1999-2009", "2010-2019", "2020-2026", "2010-2026", "1999-2026", "unseen"]


def brief(equity: pd.Series) -> dict:
    w = window_stats(equity)
    return {k: {"cagr": round(w[k]["cagr"], 4), "sharpe": round(w[k]["sharpe"], 2), "max_dd": round(w[k]["max_dd"], 3)}
            for k in WINS if k in w}


def main() -> None:
    _panel, _tbill, _cook, data = load_inputs()
    idx = data.index
    spy = pd.Series(data.spy_close, index=idx)
    out: dict = {}
    tb = pd.Series(data.tbill, index=idx)

    # ---- 1. Cook panic state: how often, and what SPY did while it was on (open-to-open, as traded)
    spy_open = pd.Series(data.open[:, data.spy_col], index=idx)
    nxt = spy_open.shift(-2) / spy_open.shift(-1) - 1.0  # return earned by a decision on close i
    on = pd.Series(data.cook, index=idx)
    rows = {}
    for name, (a, b) in {"1999-2009": ("1999-01-04", "2009-12-31"), "2010-2019": ("2010-01-01", "2019-12-31"),
                         "2020-2026": ("2020-01-01", "2026-09-30")}.items():
        r, o = nxt.loc[a:b].dropna(), on.loc[a:b]
        o = o.reindex(r.index)
        ron, roff = r[o], r[~o]
        t = (ron.mean() - roff.mean()) / np.sqrt(ron.var() / len(ron) + roff.var() / len(roff))
        rows[name] = {"share_of_days_on": float(o.mean()), "spy_ann_return_on": float(ron.mean() * 252),
                      "spy_ann_return_off": float(roff.mean() * 252), "t_stat_difference": float(t)}
    out["cook_on_vs_off"] = rows

    # episodes
    change = on.astype(int).diff().fillna(0)
    starts, ends = idx[change == 1], idx[change == -1]
    episodes = []
    for s in starts:
        if s < pd.Timestamp("1999-01-04"):
            continue
        later = ends[ends > s]
        e = later[0] if len(later) else idx[-1]
        i0, i1 = idx.get_loc(s) + 1, min(idx.get_loc(e) + 1, len(idx) - 1)
        if i0 >= len(idx):
            continue
        ret = spy_open.iloc[i1] / spy_open.iloc[i0] - 1.0
        low = spy.iloc[i0:i1 + 1].min() / spy_open.iloc[i0] - 1.0
        episodes.append({"start": str(s.date()), "end": str(e.date()), "days": int(i1 - i0),
                         "spy_return": float(ret), "worst_point": float(low)})
    out["cook_episodes"] = episodes

    # ---- 2. Risk dial on R1 (leverage while the panic state is on) and constant leverage for comparison
    dial = {}
    for lev in (1.25, 1.5, 2.0, 3.0):
        res = bs.run(data, replace(VARIANTS["R1"], panic_lev=lev), bs.SCALABLE, RUN_START)
        dial[f"cook_{lev}x"] = brief(res.equity)
    r = spy.pct_change().fillna(0.0)
    for lev in (1.06, 1.25):
        eq = (1.0 + lev * r - (lev - 1.0) * (tb + 0.03 / 252)).cumprod()
        dial[f"constant_{lev}x_spy"] = brief(eq.loc[RUN_START:])
    out["r1_leverage_dial"] = dial

    # Independent check of R1 without the simulator: open-to-open SPY returns, 1.5x on the days
    # after a panic close, financing on the borrowed half, no trading costs.
    oo = (spy_open.shift(-1) / spy_open - 1.0).shift(1).fillna(0.0)  # return from open i-1 to open i
    lev = 1.0 + 0.5 * on.shift(2, fill_value=False).astype(float)  # position held from open i-1
    vec = (1.0 + lev * oo - (lev - 1.0) * (tb + 0.03 / 252)).cumprod()
    out["r1_vector_check"] = brief(vec.loc[RUN_START:])

    # ---- 3. Robustness of the momentum book and the panic book (not selectable)
    checks = {
        "P1 top 5": replace(VARIANTS["P1"], top_n=5, hold_rank=15),
        "P1 top 20": replace(VARIANTS["P1"], top_n=20, hold_rank=60),
        "P1 rebalance day 11": replace(VARIANTS["P1"], rebalance_day=11),
        "P2 vol window 21": replace(VARIANTS["P2"], vol_window=21),
        "P2 vol window 126": replace(VARIANTS["P2"], vol_window=126),
        "R2 10 losers": replace(VARIANTS["R2"], n_losers=10),
        "R3 10 losers": replace(VARIANTS["R3"], n_losers=10),
    }
    out["robustness"] = {k: brief(bs.run(data, cfg, bs.SCALABLE, RUN_START).equity) for k, cfg in checks.items()}

    # ---- 4. What the stock books held in the unseen weeks
    unseen = {}
    for name in ("P1", "R3"):
        res = bs.run(data, VARIANTS[name], bs.SCALABLE, RUN_START)
        t = pd.DataFrame(res.trades, columns=["date", "ticker", "delta"])
        unseen[name] = {"last_book": res.books[-1][1] if res.books else None,
                        "trades_after_sep": t[t.date > "2026-09-30"].assign(date=lambda x: x.date.dt.strftime("%Y-%m-%d"))
                        .round(3).values.tolist()[:60]}
    out["unseen_weeks"] = unseen
    (OUT / "audit.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out["r1_vector_check"]))
    print(pd.DataFrame(episodes).to_string())
    print(unseen["P1"]["last_book"])
    print(unseen["R3"]["trades_after_sep"][:30])


if __name__ == "__main__":
    main()
