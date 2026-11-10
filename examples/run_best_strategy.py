"""
Run every variant in reports/best_strategy/PREREGISTRATION.md, apply the selection rule and
write reports/best_strategy/results.json, results.md and equity.csv.

Needs the S&P 500 panel (python examples/fetch_sp500_panel.py) and Yahoo access.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from quantbt.research import best_strategy as bs
from quantbt.research.megacap_reversal import (
    cook_breadth_state,
    daily_tbill,
    load_volume_panel,
    stats,
)
from quantbt.research.wizards_new import fetch_yahoo_close
from quantbt.systems.data import daily_cash_rate, load_panel
from quantbt.systems.tsmom import TSMOM_UNIVERSE, TsmomConfig, run_tsmom

OUT = Path("reports/best_strategy")
RUN_START = "1998-01-02"
WINDOWS = {
    "1999-2009": ("1999-01-04", "2009-12-31"),
    "2010-2019": ("2010-01-01", "2019-12-31"),
    "2020-2026": ("2020-01-01", "2026-09-30"),
    "2010-2026": ("2010-01-01", "2026-09-30"),
    "1999-2026": ("1999-01-04", "2026-09-30"),
    "2008-2026": ("2008-01-02", "2026-09-30"),
    "unseen": ("2026-09-30", "2030-01-01"),
}
VARIANTS = {
    "P1": bs.Config(),
    "P2": bs.Config(risk="vol", vol_target=0.20, max_lev=1.0),
    "P3": bs.Config(risk="vol", vol_target=0.30, max_lev=2.0),
    "P4": bs.Config(rank="hi52", risk="vol", vol_target=0.20, max_lev=1.0),
    "P5": bs.Config(risk="band200"),
    "R1": bs.Config(core="spy", panic="lev_spy"),
    "R2": bs.Config(core="spy", panic="reversal"),
    "R3": bs.Config(core="spy", panic="reversal", trigger="cook_or_vix"),
    "C1": bs.Config(risk="vol", vol_target=0.20, max_lev=1.0, panic="fill"),
}
COSTS = {"scalable": bs.SCALABLE, "us_broker": bs.US_BROKER, "zero": bs.ZERO_COST}
LABELS = {
    "P1": "Momentum top 10, always in",
    "P2": "P1 + vol scaling 20%, cap 1x",
    "P3": "P1 + vol scaling 30%, cap 2x",
    "P4": "P2 ranked by 52-week high",
    "P5": "P1 + buffered 200-day rule",
    "R1": "SPY, 1.5x during Cook panic",
    "R2": "SPY, megacap losers during Cook panic",
    "R3": "R2 with Cook-or-VIX trigger",
    "C1": "P2 + idle capital into panic losers",
    "T1": "Trend sleeve alone (2008+)",
    "C2": "70% C1 + 30% trend, 20% vol target, cap 2x (2008+)",
}


def window_stats(equity: pd.Series) -> dict:
    out = {}
    for name, (a, b) in WINDOWS.items():
        part = equity.loc[a:b]
        if len(part) > 5 and part.index[0] <= pd.Timestamp(a) + pd.Timedelta(days=10):
            out[name] = stats(part)
    return out


def load_inputs(end: str | None = None):
    panel = load_volume_panel(".cache/sp500_panel/prices.parquet",
                              "data/index_membership/sp500_membership_pit_panel.csv",
                              start="1995-01-01", end=end)
    tbill = daily_tbill(fetch_yahoo_close("^IRX"), panel.close.index)
    cook = cook_breadth_state(panel.close, panel.member)
    data = bs.build_data(panel, tbill, cook["buy"], fetch_yahoo_close("^VIX"))
    return panel, tbill, cook, data


def trend_sleeve(end: pd.Timestamp) -> pd.Series:
    symbols = sorted(set(TSMOM_UNIVERSE) | {"BIL"})
    panel = load_panel(symbols, pd.Timestamp("2004-01-01").to_pydatetime(), end.to_pydatetime(), strict=True)
    names = [s for s in panel.symbols if s in TSMOM_UNIVERSE]
    weights = bs.trend_weights(panel.close[names], daily_cash_rate(panel, "BIL"), TSMOM_UNIVERSE)
    res = run_tsmom(panel, TsmomConfig(cash_symbol="BIL", cost_bps_per_side=9.5, borrow_spread_bps=300.0),
                    symbols=names, weights=weights)
    return res.equity


def qualifies(w: dict, since: str = "2010") -> tuple[bool, list[str]]:
    reasons = []
    full = "1999-2026" if "1999-2026" in w else "2008-2026"
    for win in ("2010-2019", "2020-2026"):
        if w[win]["cagr"] <= w[win]["spy_cagr"]:
            reasons.append(f"trails SPY in {win}")
    if w[full]["max_dd"] < -0.50:
        reasons.append(f"drawdown {w[full]['max_dd']:.0%} over {full}")
    if any(v["ruined"] for v in w.values() if isinstance(v, dict) and "ruined" in v):
        reasons.append("ruined")
    return (not reasons, reasons)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel, tbill, cook, data = load_inputs()
    spy = panel.close[bs.SPY].ffill()
    results: dict = {"data_end": str(data.index[-1].date()), "benchmarks": {}, "variants": {}}
    curves: dict[str, pd.Series] = {}

    spy_eq = spy.loc[RUN_START:]
    results["benchmarks"]["SPY total return"] = window_stats(spy_eq)
    results["benchmarks"]["Equal-weight members"] = window_stats(bs.equal_weight_members(data, RUN_START))
    curves["SPY"] = spy_eq
    spy_cagr = {k: v["cagr"] for k, v in results["benchmarks"]["SPY total return"].items()}

    for name, cfg in VARIANTS.items():
        entry = {"label": LABELS[name], "config": asdict(cfg)}
        for cost_name, cost in COSTS.items():
            res = bs.run(data, cfg, cost, RUN_START)
            scored = res.equity.loc["1999-01-04":"2026-09-30"]
            years = (scored.index[-1] - scored.index[0]).days / 365.25
            n_orders = sum(1 for t in res.trades if pd.Timestamp("1999-01-04") <= t[0] <= pd.Timestamp("2026-09-30"))
            entry[cost_name] = {
                "windows": window_stats(res.equity),
                "orders_per_year": n_orders / years,
                "avg_exposure": float(res.exposure.loc["1999-01-04":"2026-09-30"].mean()),
            }
            if cost_name == "scalable":
                curves[name] = res.equity
                pd.DataFrame(res.trades, columns=["date", "ticker", "delta_over_equity"]).to_csv(
                    OUT / f"trades_{name}.csv", index=False, float_format="%.5f")
            print(name, cost_name, {k: round(v["cagr"] * 100, 1) for k, v in entry[cost_name]["windows"].items()})
        results["variants"][name] = entry

    trend = trend_sleeve(data.index[-1])
    trend = trend.loc["2008-01-02":]
    c1 = curves["C1"].loc["2008-01-02":]
    c2 = bs.mix_with_vol_target(c1, trend, tbill)
    for name, eq in (("T1", trend), ("C2", c2)):
        results["variants"][name] = {"label": LABELS[name], "scalable": {"windows": window_stats(eq)}}
        curves[name] = eq
        print(name, {k: round(v["cagr"] * 100, 1) for k, v in results["variants"][name]["scalable"]["windows"].items()})

    # ---- selection rule
    table = []
    for name, entry in results["variants"].items():
        w = entry["scalable"]["windows"]
        for win in w:
            w[win]["spy_cagr"] = spy_cagr.get(win)
        ok, reasons = qualifies(w)
        entry["qualifies"], entry["fail_reasons"] = ok, reasons
        table.append((name, ok, w["2010-2026"]["cagr"], len(reasons)))
    passed = sorted([t for t in table if t[1]], key=lambda t: -t[2])
    results["winner"] = passed[0][0] if passed else None
    # Closest = fewest failed rules, then the highest 2010-2026 CAGR.
    results["closest"] = None if passed else min(table, key=lambda t: (t[3], -t[2]))[0]
    print("winner:", results["winner"], "| qualifying:", [t[0] for t in passed])

    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    pd.DataFrame(curves).loc["1999-01-04":].to_csv(OUT / "equity.csv", float_format="%.6g")
    cook.to_csv(OUT / "cook_state.csv", float_format="%.5f")
    write_markdown(results)


def pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:.1f}%"


def write_markdown(results: dict) -> None:
    wins = ["1999-2009", "2010-2019", "2020-2026", "2010-2026"]
    lines = ["# Results (generated by examples/run_best_strategy.py)", "",
             f"Data through {results['data_end']}. CAGR / Sharpe (rf=0) / worst drawdown per window.", ""]

    def row(name: str, w: dict, extra: str = "") -> str:
        cells = []
        for win in wins:
            s = w.get(win)
            cells.append(f"{pct(s['cagr'])} / {s['sharpe']:.2f} / {pct(s['max_dd'])}" if s else "")
        full = w.get("1999-2026") or w.get("2008-2026")
        return f"| {name} | " + " | ".join(cells) + f" | {pct(full['max_dd'])} |{extra}"

    head = "| | " + " | ".join(wins) + " | Worst DD, full run |"
    for cost in ("scalable", "us_broker", "zero"):
        lines += [f"## Costs: {cost}", "", head + (" Orders/yr | Avg exposure | Qualifies |" if cost == "scalable" else ""),
                  "|---" * (len(wins) + 2) + ("|---|---|---|" if cost == "scalable" else "|")]
        for name, w in results["benchmarks"].items():
            lines.append(row(name, w, " | | |" if cost == "scalable" else ""))
        for name, entry in results["variants"].items():
            if cost not in entry:
                continue
            extra = ""
            if cost == "scalable":
                q = "yes" if entry["qualifies"] else "no: " + "; ".join(entry["fail_reasons"])
                opy = entry[cost].get("orders_per_year")
                exp = entry[cost].get("avg_exposure")
                extra = f" {opy:.0f} | {exp:.2f} | {q} |" if opy is not None else f" | | {q} |"
            lines.append(row(f"{name} {entry['label']}", entry[cost]["windows"], extra))
        lines.append("")
    lines += [f"**Winner by the pre-registered rule: {results['winner'] or 'none'}**"
              + ("" if results["winner"] else f" (closest: {results['closest']})"), ""]
    lines += ["## Calendar years at Scalable costs", "",
              "| Year | SPY | " + " | ".join(results["variants"]) + " |", "|---" * (len(results["variants"]) + 2) + "|"]
    spy_cal = results["benchmarks"]["SPY total return"]["1999-2026"]["calendar"]
    for year in spy_cal:
        cells = []
        for entry in results["variants"].values():
            w = entry["scalable"]["windows"]
            cal = (w.get("1999-2026") or w.get("2008-2026"))["calendar"]
            cells.append(pct(cal.get(year)))
        lines.append(f"| {year} | {pct(spy_cal[year])} | " + " | ".join(cells) + " |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
