"""Run Turtle System 1, System 2 and 12-month TSMOM with their published parameters.

Usage:
    python examples/run_turtle_tsmom.py                      # Yahoo via the repo cache
    python examples/run_turtle_tsmom.py --data-dir path/csv  # <SYMBOL>.csv files with OHLC + adj_close

All parameters are fixed in the configs; this script has no tuning knobs on purpose.
The evaluation window is declared up front: 2008-01-01 onward (every instrument's
warm-up is done by then), split into 2008-2019 and 2020-onward as a hold-out.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from quantbt.systems import TSMOM_UNIVERSE, TURTLE_UNIVERSE, TsmomConfig, TurtleConfig, load_panel, run_tsmom, run_turtle
from quantbt.systems.metrics import summarize

DATA_START = datetime(2004, 1, 1)
EVAL_START = pd.Timestamp("2008-01-01")
HOLDOUT_START = pd.Timestamp("2020-01-01")
CASH_SYMBOL = "BIL"
BENCHMARK = "SPY"


def windows(series: pd.Series) -> dict[str, pd.Series]:
    s = series[series.index >= EVAL_START]
    return {
        "full": s,
        "2008_2019": s[s.index < HOLDOUT_START],
        "2020_on": s[s.index >= HOLDOUT_START],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out", type=Path, default=Path("reports/turtle_tsmom"))
    args = parser.parse_args()
    end = datetime.strptime(args.end, "%Y-%m-%d")

    symbols = sorted(set(TURTLE_UNIVERSE) | set(TSMOM_UNIVERSE) | {CASH_SYMBOL, BENCHMARK})
    panel = load_panel(symbols, DATA_START, end, data_dir=args.data_dir)
    missing = sorted(set(symbols) - set(panel.symbols))
    if missing:
        print(f"WARNING: no data for {missing}")

    s1 = run_turtle(panel, TurtleConfig(system=1, cash_symbol=CASH_SYMBOL))
    s2 = run_turtle(panel, TurtleConfig(system=2, cash_symbol=CASH_SYMBOL))
    ts = run_tsmom(panel, TsmomConfig(cash_symbol=CASH_SYMBOL))

    # Turtles traded both systems; 50/50 capital split, rebalanced daily.
    combo_ret = 0.5 * s1.equity.pct_change().fillna(0.0) + 0.5 * s2.equity.pct_change().fillna(0.0)
    combo = (1.0 + combo_ret).cumprod() * s1.config.initial_equity
    combo_lev = 0.5 * s1.gross_leverage + 0.5 * s2.gross_leverage
    bench = panel.close[BENCHMARK].dropna()

    curves = {
        "turtle_system_1": (s1.equity, s1.gross_leverage),
        "turtle_system_2": (s2.equity, s2.gross_leverage),
        "turtle_combined_50_50": (combo, combo_lev),
        "tsmom_12m_25etf": (ts.equity, ts.gross_leverage),
        "spy_buy_and_hold": (bench, None),
    }
    results: dict[str, dict] = {}
    for name, (equity, lev) in curves.items():
        results[name] = {
            label: summarize(curve, lev) for label, curve in windows(equity).items() if len(curve) > 2
        }
    results["_notes"] = {
        "symbols_missing": missing,
        "turtle_trades_s1": int(len(s1.trades)),
        "turtle_trades_s2": int(len(s2.trades)),
        "cost_bps_per_side": s1.config.cost_bps_per_side,
        "cash_proxy": CASH_SYMBOL,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(results, indent=2))
    pd.DataFrame({name: eq for name, (eq, _) in curves.items()}).to_csv(args.out / "equity_curves.csv")
    s1.trades.to_csv(args.out / "turtle_s1_trades.csv", index=False)
    s2.trades.to_csv(args.out / "turtle_s2_trades.csv", index=False)

    rows = []
    for name, by_window in results.items():
        if name.startswith("_"):
            continue
        for label, m in by_window.items():
            rows.append(
                {
                    "strategy": name,
                    "window": label,
                    "CAGR": f"{m['cagr']:.1%}",
                    "Sharpe": f"{m['sharpe']:.2f}",
                    "MaxDD": f"{m['max_drawdown']:.1%}",
                    "Vol": f"{m['annual_vol']:.1%}",
                    "AvgLev": f"{m.get('avg_gross_leverage', 1.0):.2f}",
                }
            )
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    (args.out / "summary.txt").write_text(table.to_string(index=False) + "\n")


if __name__ == "__main__":
    main()
