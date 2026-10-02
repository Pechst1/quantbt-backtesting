"""
Estimate how much survivorship bias is left in the S&P 500 price panel.

Some former members have no price history (mostly long-gone acquisitions and
bankruptcies), so any strategy that can only pick from names with prices gets a
slightly better universe than the real index. This compares an equal-weight basket
of the members that do have prices with RSP (the real equal-weight S&P 500 ETF).
The yearly gap is a rough upper bound on the bias handed to a long-only stock picker.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--output", default="reports/minervini/sp500_panel_bias_check.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prices = pd.read_parquet(args.prices, columns=["date", "ticker", "adj_close", "volume"])
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices[prices["date"] >= "1996-01-01"]
    adj = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    rets = adj.pct_change(fill_method=None)

    spells = pd.read_csv(args.membership, parse_dates=["effective_from", "effective_to"])
    member = pd.DataFrame(False, index=rets.index, columns=rets.columns)
    all_members = pd.Series(0, index=rets.index)
    for row in spells.itertuples():
        end = row.effective_to if pd.notna(row.effective_to) else rets.index[-1]
        window = (rets.index >= row.effective_from) & (rets.index <= end)
        all_members[window] += 1
        if row.symbol in member.columns:
            member.loc[window, row.symbol] = True

    # Membership known at the prior close decides today's basket.
    held = member.shift(1, fill_value=False) & rets.notna()
    ew = rets.where(held).mean(axis=1)
    coverage = held.sum(axis=1) / all_members.shift(1)

    rsp = rets["RSP"]
    both = pd.concat({"ew_available": ew, "rsp": rsp}, axis=1).dropna()
    yearly = (1 + both).groupby(both.index.year).prod() - 1
    yearly["gap"] = yearly["ew_available"] - yearly["rsp"]
    yearly["coverage"] = coverage.groupby(coverage.index.year).mean().reindex(yearly.index)

    def cagr(r: pd.Series) -> float:
        years = len(r) / 252
        return float((1 + r).prod() ** (1 / years) - 1)

    periods = {"2003-2009": ("2003-05-01", "2009-12-31"), "2010-2019": ("2010-01-01", "2019-12-31"), "2020-2026": ("2020-01-01", "2026-12-31")}
    summary = {
        name: {
            "ew_available_cagr": cagr(both.loc[lo:hi, "ew_available"]),
            "rsp_cagr": cagr(both.loc[lo:hi, "rsp"]),
            "price_coverage": float(coverage.loc[lo:hi].mean()),
        }
        for name, (lo, hi) in periods.items()
    }
    summary["coverage_by_year"] = {int(k): round(float(v), 3) for k, v in coverage.groupby(coverage.index.year).mean().items() if np.isfinite(v)}
    summary["yearly_gap_vs_rsp"] = {int(k): round(float(v), 4) for k, v in yearly["gap"].items()}

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in periods}, indent=2))


if __name__ == "__main__":
    main()
