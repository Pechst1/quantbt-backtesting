"""
Run every pre-registered variant in reports/wizards_new/PREREGISTRATION.md and write
reports/wizards_new/results.json plus results.md (tables only; the reading is in README.md).

Needs the S&P 500 panel (python examples/fetch_sp500_panel.py) and network access to
Yahoo's chart API for ^VIX, ^VIX3M, ^GSPC, ^IRX, SVXY and VIXY (cached in .cache/yahoo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quantbt.research.wizards_new import (
    MeanReversionConfig,
    StatArbConfig,
    daily_tbill,
    equal_weight_members,
    fetch_yahoo_close,
    load_panel,
    period_stats,
    run_mean_reversion,
    run_stat_arb,
    run_vrp,
    vrp_signals,
)

OUT = Path("reports/wizards_new")
END = "2026-09-30"
STOCK_PERIODS = {"in_sample": ("1999-01-01", "2019-12-31"), "hold_out": ("2020-01-01", END)}
VRP_PERIODS = {"in_sample": ("2011-11-01", "2019-12-31"), "hold_out": ("2020-01-01", END)}


def stats(equity: pd.Series, periods: dict) -> dict:
    return {name: period_stats(equity, a, b) for name, (a, b) in periods.items()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    irx = fetch_yahoo_close("^IRX")
    results: dict[str, dict] = {"H1": {}, "H2": {}, "H3": {}, "H4": {}}
    curves: dict[str, pd.Series] = {}

    # ------------------------------------------------------------------ H2 (fast, first)
    vix, vix3m, spx = fetch_yahoo_close("^VIX"), fetch_yahoo_close("^VIX3M"), fetch_yahoo_close("^GSPC")
    svxy, vixy, spy_y = fetch_yahoo_close("SVXY"), fetch_yahoo_close("VIXY"), fetch_yahoo_close("SPY")
    svxy = svxy.loc[:END]
    sig = vrp_signals(vix, vix3m, spx)
    tb_v = daily_tbill(irx, svxy.index)
    always = pd.Series(True, index=svxy.index)
    h2 = {
        "V1 buy-and-hold SVXY": run_vrp(always, svxy, vixy, tb_v),
        "V2 roll-yield, else VIXY": run_vrp(sig["roll"], svxy, vixy, tb_v, off="long_vol"),
        "V3 VRP, else VIXY": run_vrp(sig["vrp"], svxy, vixy, tb_v, off="long_vol"),
        "V4 roll-yield, else T-bills": run_vrp(sig["roll"], svxy, vixy, tb_v, off="cash"),
        "V5 VRP, else T-bills": run_vrp(sig["vrp"], svxy, vixy, tb_v, off="cash"),
        "V6 VRP, 50% SVXY": run_vrp(sig["vrp"], svxy, vixy, tb_v, off="cash", on_weight=0.5),
    }
    spy_v = spy_y.reindex(svxy.index).ffill()
    results["H2"]["SPY total return"] = stats(spy_v, VRP_PERIODS)
    for name, eq in h2.items():
        eq = eq.loc["2011-10-31":]
        results["H2"][name] = stats(eq, VRP_PERIODS)
        episodes = {}
        for label, (a, b) in {"2018-02 Volmageddon": ("2018-01-26", "2018-02-28"),
                              "2020-02/03 COVID": ("2020-02-14", "2020-03-31")}.items():
            window = eq.loc[:b]
            base = window.loc[:a].iloc[-1]
            episodes[label] = float(window.loc[a:].min() / base - 1.0)
        results["H2"][name]["episodes"] = episodes
        curves[f"H2 {name}"] = eq
    for name in ("roll", "vrp"):
        s = sig[name].reindex(svxy.index).dropna()
        results["H2"][f"share of days short vol ({name})"] = float(s.loc["2011-11-01":].astype(float).mean())

    # ------------------------------------------------------------------ panel
    panel = load_panel(".cache/sp500_panel/prices.parquet",
                       "data/index_membership/sp500_membership_pit_panel.csv",
                       start="1997-06-01", end=END)
    spy = panel.close["SPY"]
    assert not panel.member[["SPY", "QQQ", "RSP"]].any().any()  # ETFs are never members
    tb = daily_tbill(irx, panel.close.index)
    results["H1"]["SPY total return"] = stats(spy, STOCK_PERIODS)
    ew = equal_weight_members(panel)
    results["H1"]["Equal-weight members"] = stats(ew, STOCK_PERIODS)
    results["H3"]["SPY total return"] = results["H1"]["SPY total return"]

    # ------------------------------------------------------------------ H1
    h1 = {
        "A RSI2<5, exit close>SMA5, 10 slots": MeanReversionConfig(),
        "B RSI2<5, Parker exit (up close / 5 days)": MeanReversionConfig(exit_rule="parker"),
        "C RSI2<10, exit close>SMA5": MeanReversionConfig(rsi_entry=10.0),
        "D as A + SPY>200d for entries": MeanReversionConfig(spy_filter=True),
        "E as A, 20 slots": MeanReversionConfig(slots=20),
        "A at 20 bps per side": MeanReversionConfig(cost_bps=20.0),
    }
    for name, cfg in h1.items():
        eq, trades = run_mean_reversion(panel, cfg, tb, spy_close=spy)
        res = stats(eq, STOCK_PERIODS)
        res["trades"] = int(len(trades))
        if len(trades):
            res["win_rate"] = float((trades["ret"] > 0).mean())
            res["avg_trade"] = float(trades["ret"].mean())
            res["avg_days"] = float(trades["days"].mean())
        results["H1"][name] = res
        curves[f"H1 {name}"] = eq
        if name.startswith("A RSI2"):
            trades.to_csv(OUT / "h1_A_trades.csv", index=False)
        print(name, json.dumps(res, default=float)[:300], flush=True)

    # ------------------------------------------------------------------ H3
    h3 = {
        "S1 decile long/short": StatArbConfig(quantile=0.10),
        "S2 quintile long/short": StatArbConfig(quantile=0.20),
        "S3 decile losers, long only": StatArbConfig(quantile=0.10, long_only=True),
    }
    for name, cfg in h3.items():
        eq = run_stat_arb(panel, cfg, spy, tb)
        results["H3"][name] = stats(eq, STOCK_PERIODS)
        curves[f"H3 {name}"] = eq
        print(name, results["H3"][name], flush=True)

    # ------------------------------------------------------------------ H4 (slower reversal)
    results["H4"]["SPY total return"] = results["H1"]["SPY total return"]
    results["H4"]["Equal-weight members"] = results["H1"]["Equal-weight members"]
    h4 = {
        "R1 weekly, 2-week cohorts, long/short": StatArbConfig(cohorts=2),
        "R2 weekly, 4-week cohorts, long/short": StatArbConfig(cohorts=4),
        "R3 monthly 21-day reversal, long/short": StatArbConfig(lookback=21, frequency="M"),
        "R4 as R2, long-only losers": StatArbConfig(cohorts=4, long_only=True),
        "R5 as R3, long-only losers": StatArbConfig(lookback=21, frequency="M", long_only=True),
    }
    for name, cfg in h4.items():
        eq = run_stat_arb(panel, cfg, spy, tb)
        results["H4"][name] = stats(eq, STOCK_PERIODS)
        curves[f"H4 {name}"] = eq
        print(name, results["H4"][name], flush=True)

    # Diagnostics, not variants: the same rules with zero trading costs, to show how much
    # of the raw edge the costs absorb.
    results["diagnostics_zero_cost"] = {}
    eq, _ = run_mean_reversion(panel, MeanReversionConfig(cost_bps=0.0), tb, spy_close=spy)
    results["diagnostics_zero_cost"]["H1 A at 0 bps"] = stats(eq, STOCK_PERIODS)
    eq = run_stat_arb(panel, StatArbConfig(cost_bps=0.0), spy, tb)
    results["diagnostics_zero_cost"]["H3 S1 at 0 bps"] = stats(eq, STOCK_PERIODS)
    for name, cfg in {"H4 R2 at 0 bps": StatArbConfig(cohorts=4, cost_bps=0.0),
                      "H4 R3 at 0 bps": StatArbConfig(lookback=21, frequency="M", cost_bps=0.0)}.items():
        results["diagnostics_zero_cost"][name] = stats(run_stat_arb(panel, cfg, spy, tb), STOCK_PERIODS)
    print(results["diagnostics_zero_cost"], flush=True)

    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=float))
    yearly = pd.DataFrame({k: v.resample("YE").last().pct_change() for k, v in curves.items()})
    yearly["SPY"] = spy.resample("YE").last().pct_change()
    yearly.index = yearly.index.year
    yearly.round(4).to_csv(OUT / "calendar_years.csv")
    write_tables(results)


def write_tables(results: dict) -> None:
    lines = []
    for h, block in results.items():
        lines.append(f"## {h}\n")
        lines.append("| Variant | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD | Worst day (HO) |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, r in block.items():
            if not isinstance(r, dict) or "in_sample" not in r:
                continue
            i, o = r["in_sample"], r["hold_out"]
            lines.append(f"| {name} | {i['cagr']:.1%} | {i['sharpe']:.2f} | {i['max_dd']:.0%} | "
                         f"{o['cagr']:.1%} | {o['sharpe']:.2f} | {o['max_dd']:.0%} | {o['worst_day']:.1%} |")
        lines.append("")
    (OUT / "results.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
