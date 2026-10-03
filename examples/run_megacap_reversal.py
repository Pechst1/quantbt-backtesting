"""
Run every pre-registered variant in reports/megacap_reversal/PREREGISTRATION.md and write
reports/megacap_reversal/results.json, results.md and the equity curves of the headline runs.

Needs the S&P 500 panel (python examples/fetch_sp500_panel.py) and Yahoo access for SPY and ^IRX.
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
PERIODS = {"in_sample": ("1999-01-01", "2019-12-31"), "hold_out": ("2020-01-01", "2026-09-30")}
ACCOUNTS = [10_000, 25_000, 50_000, 100_000]
VARIANTS = {
    "M1": dict(top_n=100, trend="none", cook=False),
    "M2": dict(top_n=100, trend="200d", cook=False),
    "M3": dict(top_n=100, trend="200d", cook=True),
    "M4": dict(top_n=100, trend="200d", cook=True, cook_leverage=1.5),
    "M5": dict(top_n=50, trend="200d", cook=True),
    "M6": dict(top_n=100, trend="tsmom", cook=True),
}
COSTS = {f"EUR {a // 1000}k": dict(account=a) for a in ACCOUNTS}
COSTS["diag: 6 bps, no fee"] = dict(fee=0.0, account=25_000)
COSTS["diag: 0 cost"] = dict(fee=0.0, spread_bps=0.0, account=25_000)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_volume_panel(".cache/sp500_panel/prices.parquet",
                              "data/index_membership/sp500_membership_pit_panel.csv",
                              start="1995-01-01", end=PERIODS["hold_out"][1])
    spy = fetch_yahoo_close("SPY").loc[: PERIODS["hold_out"][1]]
    irx = fetch_yahoo_close("^IRX")
    tbill = daily_tbill(irx, panel.close.index)
    cook = cook_breadth_state(panel.close, panel.member)
    cook.to_csv(OUT / "cook_state.csv")
    inp = build_inputs(panel, spy, tbill, cook["buy"])

    results: dict = {"benchmarks": {}, "variants": {}}
    curves = {}
    for period, (a, b) in PERIODS.items():
        spy_p = spy.reindex(inp.index).ffill().loc[a:b]
        results["benchmarks"].setdefault("SPY total return", {})[period] = stats(spy_p)
        for n in (100, 50):
            ew = equal_weight_megacaps(inp, n, a, b)
            results["benchmarks"].setdefault(f"Equal-weight top {n}", {})[period] = stats(ew)
        curves[f"SPY {period}"] = spy_p / spy_p.iloc[0]

    for name, params in VARIANTS.items():
        results["variants"][name] = {"params": params}
        for cost_name, cost in COSTS.items():
            row = {}
            for period, (a, b) in PERIODS.items():
                cfg = MegacapConfig(**params, **cost)
                res = run_megacap(inp, cfg, a, b)
                years = (res.equity.index[-1] - res.equity.index[0]).days / 365.25
                avg_eq = float(res.equity.mean())
                row[period] = {
                    **stats(res.equity),
                    "avg_exposure": float(res.exposure.mean()),
                    "orders_per_year": res.orders / years,
                    "fees_pct_per_year": res.fees / years / avg_eq,
                    "spread_pct_per_year": res.spread_cost / years / avg_eq,
                    "cook_days": res.days_cook,
                }
                if cost_name == "EUR 25k":
                    curves[f"{name} {period}"] = res.equity / res.equity.iloc[0]
            results["variants"][name][cost_name] = row
            print(name, cost_name, {p: round(row[p]["cagr"] * 100, 1) for p in row})

    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    pd.DataFrame(curves).to_csv(OUT / "equity_25k.csv", float_format="%.5f")
    write_markdown(results)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_markdown(results: dict) -> None:
    lines = ["# Results (generated)", ""]
    head = "| | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD |"
    sep = "|---|---|---|---|---|---|---|"

    def cells(r):
        i, h = r["in_sample"], r["hold_out"]
        return (f"{pct(i['cagr'])} | {i['sharpe']:.2f} | {pct(i['max_dd'])} | "
                f"{pct(h['cagr'])} | {h['sharpe']:.2f} | {pct(h['max_dd'])}")

    lines += ["## Benchmarks (no costs)", "", head, sep]
    for name, r in results["benchmarks"].items():
        lines.append(f"| {name} | {cells(r)} |")
    for name, v in results["variants"].items():
        lines += ["", f"## {name} {v['params']}", "",
                  head[:-1] + " Orders/yr IS/HO | Fees %/yr IS/HO | Exposure IS/HO |",
                  sep + "---|---|---|"]
        for cost_name, r in v.items():
            if cost_name == "params":
                continue
            i, h = r["in_sample"], r["hold_out"]
            lines.append(
                f"| {cost_name} | {cells(r)} | {i['orders_per_year']:.0f} / {h['orders_per_year']:.0f} | "
                f"{pct(i['fees_pct_per_year'])} / {pct(h['fees_pct_per_year'])} | "
                f"{i['avg_exposure']:.2f} / {h['avg_exposure']:.2f} |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
