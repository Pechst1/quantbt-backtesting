"""
Independent vectorized cross-check of the RS leaders backtest (no engine, no costs).

Top-N by 12-1 momentum among point-in-time members, equal weight, fully rebalanced at
each month's first close and earning from the next close; optional SPY 200-day filter.
It skips the stop and the no-resize rule, so it should land near variants A and B,
not match them exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_minervini_trend_template import IntervalMembership  # noqa: E402
from run_rs_momentum import by_period  # noqa: E402

from quantbt.data.panel import ParquetPanelSource


def main() -> None:
    membership = IntervalMembership(Path("data/index_membership/sp500_membership_pit_panel.csv"))
    source = ParquetPanelSource(".cache/sp500_panel/prices.parquet", symbols=membership.symbols + ["SPY"])
    closes = pd.DataFrame({s: f["adj_close"] for s, f in source._frames.items()}).sort_index().loc["1998-01-01":"2026-09-30"]
    spy = closes.pop("SPY")
    rets = closes.pct_change(fill_method=None).fillna(0.0)
    mom = closes.ffill().shift(21) / closes.ffill().shift(252) - 1.0
    spy_ok = spy > spy.rolling(200).mean()
    month_starts = closes.index.to_series().groupby(closes.index.to_period("M")).first().iloc[1:]
    out = {}
    for name, use_filter in (("vector_A", False), ("vector_B", True)):
        weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
        for ts in month_starts:
            row = mom.loc[ts]
            alive = closes.loc[ts].notna()
            ok = [s for s in row.index if alive[s] and np.isfinite(row[s]) and membership.is_member(s, ts.date())]
            w = pd.Series(0.0, index=closes.columns)
            if ok and (spy_ok.loc[ts] or not use_filter):
                top = row[ok].drop_duplicates().nlargest(10).index
                w[top] = 0.1
            weights.loc[ts] = w
        weights = weights.ffill().shift(1).fillna(0.0)
        equity = (1.0 + (weights * rets).sum(axis=1)).cumprod()
        out[name] = by_period(equity[equity.index >= "1999-01-01"])
    Path("reports/rs_momentum/vector_check.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
