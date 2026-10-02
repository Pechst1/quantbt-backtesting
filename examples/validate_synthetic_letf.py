"""
Compare the synthetic daily-reset funds with the real ones (SSO, UPRO, QLD, TQQQ).

Real prices come from Massive (MASSIVE_API_KEY), whose plan here starts 2021-10-04, so the
check covers 2021-10 onward. The cost model is the pre-declared one; this only measures it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.data.leveraged import load_fred_rate, synthetic_leveraged_ohlc
from quantbt.data.panel import ParquetPanelSource

PAIRS = {"SSO": ("SPY", 2), "UPRO": ("SPY", 3), "QLD": ("QQQ", 2), "TQQQ": ("QQQ", 3)}
START, END = "2021-10-04", "2026-09-30"


def massive_total_return(symbol: str) -> pd.Series:
    """Daily closes adjusted for splits, with dividends reinvested."""
    key = os.environ["MASSIVE_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}

    def get(url: str) -> dict:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
            return json.loads(resp.read())

    aggs = get(f"https://api.massive.com/v2/aggs/ticker/{symbol}/range/1/day/{START}/{END}?adjusted=true&limit=50000")
    close = pd.Series({pd.Timestamp(r["t"], unit="ms").normalize(): r["c"] for r in aggs["results"]}).sort_index()
    divs = get(f"https://api.massive.com/v3/reference/dividends?ticker={symbol}&limit=1000").get("results", [])
    cash = pd.Series(0.0, index=close.index)
    for d in divs:
        ex = pd.Timestamp(d["ex_dividend_date"])
        if close.index[0] < ex <= close.index[-1]:
            cash.loc[close.index[close.index.searchsorted(ex)]] += float(d["cash_amount"])
    ret = (close + cash) / close.shift(1) - 1.0
    return (1.0 + ret.fillna(0.0)).cumprod()


def main() -> None:
    rate = load_fred_rate("DTB3")
    panel = ParquetPanelSource(".cache/sp500_panel/prices.parquet", symbols=["SPY", "QQQ"])
    rows = {}
    for fund, (base, lev) in PAIRS.items():
        real = massive_total_return(fund)
        under = panel.fetch_ohlcv(base, pd.Timestamp(START), pd.Timestamp(END), "1d")
        synth = synthetic_leveraged_ohlc(under, lev, rate)["close"]
        idx = real.index.intersection(synth.index)
        real, synth = real.loc[idx], synth.loc[idx]
        r_real, r_syn = real.pct_change().dropna(), synth.pct_change().dropna()
        years = (idx[-1] - idx[0]).days / 365.25
        cagr = lambda s: (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1
        rows[fund] = {
            "days": len(idx),
            "real_cagr": round(float(cagr(real)), 4),
            "synthetic_cagr": round(float(cagr(synth)), 4),
            "annual_gap_synth_minus_real": round(float(cagr(synth) - cagr(real)), 4),
            "daily_return_corr": round(float(np.corrcoef(r_real, r_syn)[0, 1]), 5),
            "daily_tracking_error_bps": round(float((r_syn - r_real).std() * 1e4), 1),
        }
    out = Path("reports/leveraged_trend/synthetic_vs_real.json")
    out.write_text(json.dumps({"window": [START, END], "funds": rows}, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
