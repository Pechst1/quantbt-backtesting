"""
Cost sensitivity for the reversal variants in reports/wizards_new: rerun H1 A, H3 S1 and
H4 R4 at several per-side costs. Writes reports/wizards_new/cost_sweep.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from quantbt.research.wizards_new import (
    MeanReversionConfig,
    StatArbConfig,
    daily_tbill,
    fetch_yahoo_close,
    load_panel,
    period_stats,
    run_mean_reversion,
    run_stat_arb,
)

END = "2026-09-30"
PERIODS = {"in_sample": ("1999-01-01", "2019-12-31"), "hold_out": ("2020-01-01", END)}
COSTS = [0.0, 2.0, 5.0, 10.0, 25.0]


def main() -> None:
    panel = load_panel(".cache/sp500_panel/prices.parquet",
                       "data/index_membership/sp500_membership_pit_panel.csv",
                       start="1997-06-01", end=END)
    spy = panel.close["SPY"]
    tb = daily_tbill(fetch_yahoo_close("^IRX"), panel.close.index)
    out: dict[str, dict] = {}
    for bps in COSTS:
        runs = {
            "H1 A": run_mean_reversion(panel, MeanReversionConfig(cost_bps=bps), tb, spy_close=spy)[0],
            "H3 S1": run_stat_arb(panel, StatArbConfig(cost_bps=bps), spy, tb),
            "H4 R4": run_stat_arb(panel, StatArbConfig(cohorts=4, long_only=True, cost_bps=bps), spy, tb),
        }
        for name, eq in runs.items():
            out.setdefault(name, {})[f"{bps:g} bps"] = {p: period_stats(eq, a, b) for p, (a, b) in PERIODS.items()}
            r = out[name][f"{bps:g} bps"]
            print(f"{name} {bps:>4g} bps  IS {r['in_sample']['cagr']:.1%}  HO {r['hold_out']['cagr']:.1%}", flush=True)
    Path("reports/wizards_new/cost_sweep.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
