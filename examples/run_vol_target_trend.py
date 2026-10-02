"""Turtle S1/S2 and 12-month TSMOM with a portfolio vol target and a 3x gross cap.

Every setting is fixed in reports/vol_target_trend/PREREGISTRATION.md, committed
before the first run. This script runs exactly the variants declared there:
each system at native published sizing, at a 15% and at a 20% vol target.

Usage:
    python examples/run_vol_target_trend.py            # Yahoo via the repo cache
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from quantbt.systems import TSMOM_UNIVERSE, TURTLE_UNIVERSE, TsmomConfig, TurtleConfig, load_panel, run_tsmom, run_turtle
from quantbt.systems.metrics import summarize
from quantbt.systems.overlay import VolTarget

DATA_START = datetime(2004, 1, 1)
EVAL_START = pd.Timestamp("2008-01-01")
HOLDOUT_START = pd.Timestamp("2020-01-01")
CASH_SYMBOL = "BIL"
BENCHMARK = "SPY"
COST_BPS = 9.5
BORROW_SPREAD_BPS = 50.0
MAX_GROSS = 3.0
TARGETS = {"native": None, "vt15": 0.15, "vt20": 0.20}


def windows(series: pd.Series) -> dict[str, pd.Series]:
    s = series[series.index >= EVAL_START]
    return {
        "full": s,
        f"{EVAL_START.year}_{HOLDOUT_START.year - 1}": s[s.index < HOLDOUT_START],
        f"{HOLDOUT_START.year}_on": s[s.index >= HOLDOUT_START],
    }


def combine(a: tuple[pd.Series, pd.Series], b: tuple[pd.Series, pd.Series]) -> tuple[pd.Series, pd.Series]:
    """50/50 capital split rebalanced daily, as the Turtles split between systems."""
    ret = 0.5 * a[0].pct_change().fillna(0.0) + 0.5 * b[0].pct_change().fillna(0.0)
    return (1.0 + ret).cumprod() * a[0].iloc[0], 0.5 * a[1] + 0.5 * b[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--out", type=Path, default=Path("reports/vol_target_trend"))
    args = parser.parse_args()
    end = datetime.strptime(args.end, "%Y-%m-%d")

    symbols = sorted(set(TURTLE_UNIVERSE) | set(TSMOM_UNIVERSE) | {CASH_SYMBOL, BENCHMARK})
    panel = load_panel(symbols, DATA_START, end, strict=True)

    curves: dict[str, tuple[pd.Series, pd.Series | None]] = {}
    trade_counts: dict[str, int] = {}
    for label, target in TARGETS.items():
        vt = VolTarget(target_vol=target, max_gross=MAX_GROSS) if target is not None else None
        runs = {}
        for system in (1, 2):
            cfg = TurtleConfig(
                system=system, cash_symbol=CASH_SYMBOL, cost_bps_per_side=COST_BPS, borrow_spread_bps=BORROW_SPREAD_BPS
            )
            res = run_turtle(panel, cfg, vol_target=vt)
            runs[system] = (res.equity, res.gross_leverage)
            curves[f"turtle_s{system}_{label}"] = runs[system]
            trade_counts[f"turtle_s{system}_{label}"] = int(len(res.trades))
        curves[f"turtle_50_50_{label}"] = combine(runs[1], runs[2])
        ts = run_tsmom(
            panel,
            TsmomConfig(cash_symbol=CASH_SYMBOL, cost_bps_per_side=COST_BPS, borrow_spread_bps=BORROW_SPREAD_BPS),
            vol_target=vt,
        )
        curves[f"tsmom_{label}"] = (ts.equity, ts.gross_leverage)
    curves["spy_buy_and_hold"] = (panel.close[BENCHMARK].dropna(), None)

    results: dict[str, dict] = {}
    for name, (equity, lev) in curves.items():
        results[name] = {w: summarize(c, lev) for w, c in windows(equity).items() if len(c) > 2}
    results["_notes"] = {
        "data_source": "yahoo",
        "first_bar": {s: str(panel.close[s].first_valid_index().date()) for s in panel.symbols},
        "trade_counts": trade_counts,
        "cost_bps_per_side": COST_BPS,
        "borrow_spread_bps": BORROW_SPREAD_BPS,
        "max_gross": MAX_GROSS,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(results, indent=2))
    pd.DataFrame({name: eq for name, (eq, _) in curves.items()}).loc[EVAL_START:].to_csv(
        args.out / "equity_curves.csv", float_format="%.2f"
    )

    rows = []
    for name, by_window in results.items():
        if name.startswith("_"):
            continue
        for w, m in by_window.items():
            rows.append(
                {
                    "strategy": name,
                    "window": w,
                    "CAGR": f"{m['cagr']:.1%}",
                    "Sharpe": f"{m['sharpe']:.2f}",
                    "MaxDD": f"{m['max_drawdown']:.1%}",
                    "Vol": f"{m['annual_vol']:.1%}",
                    "AvgLev": f"{m.get('avg_gross_leverage', 1.0):.2f}",
                    "MaxLev": f"{m.get('max_gross_leverage', 1.0):.2f}",
                }
            )
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    (args.out / "summary.txt").write_text(table.to_string(index=False) + "\n")


if __name__ == "__main__":
    main()
