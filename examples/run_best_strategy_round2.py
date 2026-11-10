"""
Round 2 of the best-strategy search (reports/best_strategy/PREREGISTRATION_ROUND2.md):
does the Cook panic signal generalise to other indices, and do leverage, a loss stop and
momentum-plus-panic variants add return. Writes round2.json and round2.md.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_best_strategy import OUT, RUN_START, VARIANTS, window_stats

from quantbt.research import best_strategy as bs
from quantbt.research.megacap_reversal import (
    cook_breadth_state,
    daily_tbill,
    load_volume_panel,
)
from quantbt.research.wizards_new import fetch_yahoo_close

GENERALISE = ["QQQ", "IWM", "MDY", "EFA", "EEM"]
SPLITS = {"1999-2009": ("1999-01-04", "2009-12-31"), "2010-2019": ("2010-01-01", "2019-12-31"),
          "2020-2026": ("2020-01-01", "2026-09-30"), "2010-2026": ("2010-01-01", "2026-09-30")}
P1 = VARIANTS["P1"]
ROUND2 = {
    "L1": (bs.Config(core="spy", panic="lev_spy", panic_lev=2.0), "SPY"),
    "L2": (bs.Config(core="spy", panic="lev_spy", panic_lev=2.0, panic_stop=0.07), "SPY"),
    "L3": (bs.Config(core="spy", panic="lev_spy", panic_lev=3.0, panic_stop=0.07), "SPY"),
    "Q1": (bs.Config(core="spy", index="QQQ", panic="lev_spy", panic_lev=1.5), "QQQ"),
    "Q2": (bs.Config(core="spy", index="QQQ", panic="lev_spy", panic_lev=2.0, panic_stop=0.07), "QQQ"),
    "M1": (replace(P1, panic="to_index", panic_lev=1.5), "SPY"),
    "M2": (replace(P1, panic="to_index", panic_lev=2.0, panic_stop=0.07), "SPY"),
}
LABELS = {
    "L1": "SPY; 2x in panic", "L2": "SPY; 2x in panic, 7% stop", "L3": "SPY; 3x in panic, 7% stop",
    "Q1": "QQQ; 1.5x in panic", "Q2": "QQQ; 2x in panic, 7% stop",
    "M1": "Momentum; 1.5x SPY in panic", "M2": "Momentum; 2x SPY in panic, 7% stop",
}
DD_LIMIT = -0.65


def on_off(ret: pd.Series, on: pd.Series) -> dict:
    out = {}
    for name, (a, b) in SPLITS.items():
        r = ret.loc[a:b].dropna()
        if len(r) < 500:
            continue
        o = on.reindex(r.index).fillna(False)
        ron, roff = r[o], r[~o]
        t = (ron.mean() - roff.mean()) / np.sqrt(ron.var() / len(ron) + roff.var() / len(roff))
        out[name] = {"on_ann": float(ron.mean() * 252), "off_ann": float(roff.mean() * 252),
                     "diff_ann": float((ron.mean() - roff.mean()) * 252), "t": float(t), "days_on": len(ron)}
    return out


def constant_leverage(close: pd.Series, tbill: pd.Series, lev: float) -> pd.Series:
    r = close.pct_change().fillna(0.0)
    tb = tbill.reindex(close.index).fillna(0.0)
    finance = np.where(lev > 1.0, (lev - 1.0) * (tb + 0.03 / 252), -(1.0 - lev) * tb)
    return (1.0 + lev * r - finance).cumprod()


def main() -> None:
    panel = load_volume_panel(".cache/sp500_panel/prices.parquet",
                              "data/index_membership/sp500_membership_pit_panel.csv", start="1995-01-01")
    tbill = daily_tbill(fetch_yahoo_close("^IRX"), panel.close.index)
    cook = cook_breadth_state(panel.close, panel.member)
    data = bs.build_data(panel, tbill, cook["buy"], fetch_yahoo_close("^VIX"), etfs=("QQQ",))
    idx = data.index
    on_prev = pd.Series(data.cook, index=idx).shift(1, fill_value=False)  # state at the previous close
    out: dict = {"data_end": str(idx[-1].date())}

    # ---- Part 1: the signal on indices not used with it before
    part1, series = {}, []
    for sym in GENERALISE:
        opens = panel.open[sym]
        oo = opens.shift(-1) / opens - 1.0  # open i to open i+1, earned by a decision on close i-1
        part1[sym] = on_off(oo, on_prev)
        series.append(oo)
    pooled = pd.concat(series, axis=1).mean(axis=1, skipna=False)
    part1["pooled"] = on_off(pooled, on_prev)
    positive = sum(part1[s]["2010-2026"]["diff_ann"] > 0 for s in GENERALISE)
    out["part1"] = part1
    out["part1_pass"] = bool(positive >= 4 and part1["pooled"]["2010-2026"]["t"] > 2.0)
    print("Part 1:", {s: round(part1[s]["2010-2026"]["t"], 2) for s in part1}, "pass:", out["part1_pass"])

    # ---- Part 2: variants
    bench = {s: panel.close[s].ffill().loc[RUN_START:] for s in ("SPY", "QQQ")}
    out["benchmarks"] = {s: window_stats(eq) for s, eq in bench.items()}
    out["variants"] = {}
    curves = {f"{s} buy and hold": eq for s, eq in bench.items()}
    for name, (cfg, under) in ROUND2.items():
        res = bs.run(data, cfg, bs.SCALABLE, RUN_START)
        w = window_stats(res.equity)
        scored = res.exposure.loc["1999-01-04":"2026-09-30"]
        avg_exp = float(scored.mean())
        const = window_stats(constant_leverage(bench[under], tbill, avg_exp))
        under_w = out["benchmarks"][under]
        n_orders = sum(1 for t in res.trades if pd.Timestamp("1999-01-04") <= t[0] <= pd.Timestamp("2026-09-30"))
        fails = []
        for win in ("2010-2019", "2020-2026"):
            if w[win]["cagr"] <= under_w[win]["cagr"]:
                fails.append(f"trails {under} in {win}")
        if w["2010-2026"]["cagr"] <= const["2010-2026"]["cagr"]:
            fails.append("no better than constant leverage")
        timing_ok = not fails
        if w["1999-2026"]["max_dd"] < DD_LIMIT:
            fails.append(f"drawdown {w['1999-2026']['max_dd']:.0%}")
        out["variants"][name] = {
            "label": LABELS[name], "underlying": under, "windows": w, "avg_exposure": avg_exp,
            "orders_per_year": n_orders / 27.74, "constant_leverage": const,
            "qualifies": not fails, "timing_ok": timing_ok, "fail_reasons": fails,
        }
        curves[name] = res.equity
        print(name, {k: round(v["cagr"] * 100, 1) for k, v in w.items()}, "DD", round(w["1999-2026"]["max_dd"], 2),
              "const", round(const["2010-2026"]["cagr"] * 100, 1), fails)

    ranked = sorted(out["variants"].items(), key=lambda kv: -kv[1]["windows"]["2010-2026"]["cagr"])
    qualifying = [k for k, v in ranked if v["qualifies"]]
    out["winner"] = qualifying[0] if (qualifying and out["part1_pass"]) else None
    aggressive = [k for k, v in ranked if v["timing_ok"] and not v["qualifies"]]
    out["aggressive"] = aggressive[0] if aggressive else None
    print("winner:", out["winner"], "| qualifying:", qualifying, "| aggressive:", out["aggressive"])

    # ---- robustness for the winner (not selectable)
    if out["winner"]:
        cfg, _ = ROUND2[out["winner"]]
        checks = {
            "stop 5%": (replace(cfg, panic_stop=0.05), bs.SCALABLE),
            "stop 10%": (replace(cfg, panic_stop=0.10), bs.SCALABLE),
            "no stop": (replace(cfg, panic_stop=None), bs.SCALABLE),
            "one day later": (replace(cfg, panic_delay=1), bs.SCALABLE),
            "US broker costs": (cfg, bs.US_BROKER),
            "zero costs": (cfg, bs.ZERO_COST),
            "borrow at T-bills + 1%": (cfg, replace(bs.SCALABLE, borrow_spread=0.01)),
        }
        out["robustness"] = {k: window_stats(bs.run(data, c, cost, RUN_START).equity) for k, (c, cost) in checks.items()}
        for k, w in out["robustness"].items():
            print("  ", k, {x: round(w[x]["cagr"] * 100, 1) for x in ("2010-2019", "2020-2026", "2010-2026")},
                  "DD", round(w["1999-2026"]["max_dd"], 2))

    (OUT / "round2.json").write_text(json.dumps(out, indent=2, default=str))
    pd.DataFrame(curves).loc["1999-01-04":].to_csv(OUT / "round2_equity.csv", float_format="%.6g")
    write_markdown(out)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_markdown(out: dict) -> None:
    lines = ["# Round 2 results (generated by examples/run_best_strategy_round2.py)", "",
             f"Data through {out['data_end']}.", "", "## Part 1: SPX Cook state on other indices (open-to-open, annualised)", "",
             "| | 2010-2019 on / off / t | 2020-2026 on / off / t | 2010-2026 on / off / t |", "|---|---|---|---|"]
    for sym, w in out["part1"].items():
        cells = [f"{pct(w[k]['on_ann'])} / {pct(w[k]['off_ann'])} / {w[k]['t']:.2f}" for k in ("2010-2019", "2020-2026", "2010-2026")]
        lines.append(f"| {sym} | " + " | ".join(cells) + " |")
    lines += ["", f"Part 1 passes: **{out['part1_pass']}**", "", "## Part 2: variants at Scalable costs (CAGR / Sharpe / worst drawdown)", "",
              "| | 1999-2009 | 2010-2019 | 2020-2026 | 2010-2026 | Worst DD 1999-2026 | Avg exposure | Constant leverage 2010-2026 | Orders/yr | Qualifies |",
              "|---|---|---|---|---|---|---|---|---|---|"]

    def cells(w: dict) -> str:
        return " | ".join(f"{pct(w[k]['cagr'])} / {w[k]['sharpe']:.2f} / {pct(w[k]['max_dd'])}"
                          for k in ("1999-2009", "2010-2019", "2020-2026", "2010-2026"))

    for name, w in out["benchmarks"].items():
        lines.append(f"| {name} buy and hold | {cells(w)} | {pct(w['1999-2026']['max_dd'])} | 1.00 | | | |")
    for name, v in out["variants"].items():
        q = "yes" if v["qualifies"] else "no: " + "; ".join(v["fail_reasons"])
        lines.append(f"| {name} {v['label']} | {cells(v['windows'])} | {pct(v['windows']['1999-2026']['max_dd'])} | "
                     f"{v['avg_exposure']:.2f} | {pct(v['constant_leverage']['2010-2026']['cagr'])} | {v['orders_per_year']:.0f} | {q} |")
    summary = (f"**Winner: {out['winner'] or 'none'}**. Highest return among those failing only the drawdown limit: "
               f"{out['aggressive'] or 'none'}.")
    lines += ["", summary, ""]
    if out.get("robustness"):
        lines += [f"## Robustness of {out['winner']} (not selectable)", "",
                  "| | 2010-2019 | 2020-2026 | 2010-2026 | Worst DD 1999-2026 | After 2026-09-30 |", "|---|---|---|---|---|---|"]
        for k, w in out["robustness"].items():
            eq_unseen = w.get("unseen", {})
            lines.append(f"| {k} | {pct(w['2010-2019']['cagr'])} | {pct(w['2020-2026']['cagr'])} | {pct(w['2010-2026']['cagr'])} | "
                         f"{pct(w['1999-2026']['max_dd'])} | {pct(eq_unseen.get('max_dd', 0.0))} worst dip |")
    (OUT / "round2.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
