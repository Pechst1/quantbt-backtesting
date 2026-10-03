"""
Post-hoc checks of the megacap reversal results (not pre-registered variants; nothing here
changes a rule). Writes reports/megacap_reversal/audit.json and audit.md:

1. Survivorship: the panel lacks 30-42% of S&P 500 members before 2005 (mostly names that
   collapsed or were taken over), so 1999-2019 is split into 1999-2009 and the near-complete
   2010-2019, each run from a fresh EUR 25k account and at zero cost.
2. Formation day: the weekly ranking moved from Friday to Wednesday closes.
3. Where the Cook override's return comes from: days invested only because of Cook (M3 in,
   M2 out), at zero cost.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quantbt.research.megacap_reversal import (
    MegacapConfig,
    build_inputs,
    cook_breadth_state,
    daily_tbill,
    equal_weight_megacaps,
    load_volume_panel,
    run_megacap,
    stats,
)
from quantbt.research.wizards_new import fetch_yahoo_close

OUT = Path("reports/megacap_reversal")
END = "2026-09-30"
PERIODS = {"1999-2009": ("1999-01-01", "2009-12-31"), "2010-2019": ("2010-01-01", "2019-12-31"),
           "2020-2026": ("2020-01-01", END)}
VARIANTS = {
    "M1": dict(top_n=100, trend="none", cook=False),
    "M2": dict(top_n=100, trend="200d", cook=False),
    "M3": dict(top_n=100, trend="200d", cook=True),
    "M4": dict(top_n=100, trend="200d", cook=True, cook_leverage=1.5),
    "M5": dict(top_n=50, trend="200d", cook=True),
    "M6": dict(top_n=100, trend="tsmom", cook=True),
}
COSTS = {"EUR 25k": dict(account=25_000), "0 cost": dict(fee=0.0, spread_bps=0.0)}


def short(s: dict) -> dict:
    return {k: s[k] for k in ("cagr", "sharpe", "max_dd")}


def main() -> None:
    panel = load_volume_panel(".cache/sp500_panel/prices.parquet",
                              "data/index_membership/sp500_membership_pit_panel.csv", end=END)
    spy = fetch_yahoo_close("SPY").loc[:END]
    tbill = daily_tbill(fetch_yahoo_close("^IRX"), panel.close.index)
    cook = cook_breadth_state(panel.close, panel.member)
    inputs = {"Fri": build_inputs(panel, spy, tbill, cook["buy"]),
              "Wed": build_inputs(panel, spy, tbill, cook["buy"], week_anchor="W-WED")}
    inp = inputs["Fri"]
    audit: dict = {"decades": {}, "cook_only_days": {}}
    for period, (a, b) in PERIODS.items():
        rows = {"SPY": short(stats(spy.reindex(inp.index).ffill().loc[a:b]))}
        for n in (100, 50):
            rows[f"Equal-weight top {n}"] = short(stats(equal_weight_megacaps(inp, n, a, b)))
        for name, params in VARIANTS.items():
            for cost_name, cost in COSTS.items():
                for day, inp_d in inputs.items():
                    if day == "Wed" and cost_name != "EUR 25k":
                        continue
                    label = f"{name} {cost_name}" + (" (Wednesday)" if day == "Wed" else "")
                    rows[label] = short(stats(run_megacap(inp_d, MegacapConfig(**params, **cost), a, b).equity))
        audit["decades"][period] = rows

        zero = dict(fee=0.0, spread_bps=0.0)
        m2 = run_megacap(inp, MegacapConfig(**VARIANTS["M2"], **zero), a, b)
        m3 = run_megacap(inp, MegacapConfig(**VARIANTS["M3"], **zero), a, b)
        only = (m3.exposure > 0.5) & (m2.exposure < 0.5)
        r3 = m3.equity.pct_change().fillna(0.0)[only]
        r_spy = spy.reindex(inp.index).ffill().pct_change().reindex(r3.index)
        audit["cook_only_days"][period] = {
            "days": int(only.sum()),
            "compound_return": float((1 + r3).prod() - 1),
            "spy_compound_same_days": float((1 + r_spy).prod() - 1),
            "by_year": {str(y): float(v) for y, v in (1 + r3).groupby(r3.index.year).prod().sub(1).items()},
        }
    (OUT / "audit.json").write_text(json.dumps(audit, indent=2))

    lines = ["# Audit tables (generated)", "", "CAGR / Sharpe / MaxDD, each period a fresh run.", "",
             "| | " + " | ".join(PERIODS) + " |", "|---" * (len(PERIODS) + 1) + "|"]
    for label in audit["decades"]["2010-2019"]:
        cells = []
        for period in PERIODS:
            s = audit["decades"][period][label]
            cells.append(f"{s['cagr'] * 100:.1f}% / {s['sharpe']:.2f} / {s['max_dd'] * 100:.0f}%")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Cook-only days (M3 invested, M2 in cash), zero cost:", ""]
    for period, c in audit["cook_only_days"].items():
        lines.append(f"- {period}: {c['days']} days, strategy {c['compound_return'] * 100:+.0f}%, "
                     f"SPY {c['spy_compound_same_days'] * 100:+.0f}% on the same days")
    (OUT / "audit.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
