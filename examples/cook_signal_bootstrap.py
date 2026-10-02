"""
Compare the H2 (Cook breadth) buy signals with random SPY holding periods of the same length.

Reads reports/stock_market_wizards/H2-A_trades.csv and writes H2_signal_bootstrap.json.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

spy = pd.read_parquet(".cache/sp500_panel/prices.parquet", filters=[("ticker", "=", "SPY")]).set_index("date")["adj_close"]
spy.index = pd.to_datetime(spy.index)
spy = spy["1998":"2026-09-30"]
trades = pd.read_csv("reports/stock_market_wizards/H2-A_trades.csv", parse_dates=["entry", "exit"])
idx = spy.index
starts = idx.get_indexer(trades["entry"], method="bfill")
ends = idx.get_indexer(trades["exit"], method="bfill")
durations = ends - starts
signal = spy.values[ends] / spy.values[starts] - 1.0

rng = np.random.default_rng(0)
n_draws = 20_000
means = np.empty(n_draws)
wins = np.empty(n_draws)
for k in range(n_draws):
    st = rng.integers(0, len(spy) - durations.max() - 1, len(durations))
    rr = spy.values[st + durations] / spy.values[st] - 1.0
    means[k] = rr.mean()
    wins[k] = (rr > 0).mean()
out = {
    "n": int(len(signal)),
    "signal_mean": float(signal.mean()),
    "signal_win": float((signal > 0).mean()),
    "random_mean": float(means.mean()),
    "random_win": float(wins.mean()),
    "p_mean": float((means >= signal.mean()).mean()),
    "p_win": float((wins >= (signal > 0).mean()).mean()),
}
print(out)
with open("reports/stock_market_wizards/H2_signal_bootstrap.json", "w") as fh:
    json.dump(out, fh, indent=2)
