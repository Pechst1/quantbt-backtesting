"""
Round 2, part 3 (reports/best_strategy/PREREGISTRATION_ROUND2.md): leverage rules on the
S&P 500 total return since 1928. Writes reports/best_strategy/century.json and century.md.

Data: Yahoo ^GSPC daily closes, Shiller monthly dividends, FRED TB3MS. All cached under .cache/century.
If FRED refuses the script's request, fetch it once with:
  curl -o .cache/century/tb3ms.csv "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TB3MS"
"""

from __future__ import annotations

import io
import json
import math
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("reports/best_strategy")
CACHE = Path(".cache/century")
URLS = {
    "gspc.json": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?period1=-1500000000&period2=1900000000&interval=1d",
    "shiller.csv": "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv",
    "tb3ms.csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TB3MS",
}
WINDOWS = {"1928-1949": ("1928-01-01", "1949-12-31"), "1950-1974": ("1950-01-01", "1974-12-31"),
           "1975-1998": ("1975-01-01", "1998-12-31"), "1999-2026": ("1999-01-01", "2026-09-30"),
           "1928-2026": ("1928-01-01", "2026-09-30")}
UNSEEN = ("1928-1949", "1950-1974", "1975-1998")


def fetch(name: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        req = urllib.request.Request(URLS[name], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as response:
            path.write_bytes(response.read())
    return path.read_bytes()


def load() -> pd.DataFrame:
    """Daily frame: price close, total return, cash rate (both as daily simple returns)."""
    result = json.loads(fetch("gspc.json"))["chart"]["result"][0]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    close = pd.Series(result["indicators"]["quote"][0]["close"], index=index, dtype=float).dropna()
    close = close[~close.index.duplicated(keep="last")].loc[:"2026-09-30"]

    shiller = pd.read_csv(io.BytesIO(fetch("shiller.csv")), parse_dates=["Date"]).set_index("Date")
    div_yield = (shiller["Dividend"] / shiller["SP500"]).where(shiller["Dividend"] > 0).ffill()
    long_rate = shiller["Long Interest Rate"].where(shiller["Long Interest Rate"] > 0).ffill() / 100.0
    tbill = pd.read_csv(io.BytesIO(fetch("tb3ms.csv")), parse_dates=["observation_date"]).set_index("observation_date")["TB3MS"] / 100.0

    def monthly_to_daily(series: pd.Series) -> pd.Series:
        # previous month's value, so nothing published later is used
        lagged = series.shift(1)
        return lagged.reindex(lagged.index.union(close.index)).ffill().reindex(close.index)

    cash_annual = monthly_to_daily(tbill).fillna(monthly_to_daily(long_rate))
    frame = pd.DataFrame({"close": close})
    frame["ret"] = close.pct_change().fillna(0.0) + monthly_to_daily(div_yield) / 252.0
    frame["cash"] = cash_annual / 252.0
    return frame


def simulate(frame: pd.DataFrame, wanted: pd.Series, borrow_spread: float = 0.015, cost_bps: float = 5.0,
             delay: int = 2, step: float = 0.0) -> pd.Series:
    """Equity of a daily-reset leveraged position. `wanted[t]` is read on close t and earns from day t+delay."""
    target = wanted.shift(delay).fillna(1.0).to_numpy()
    ret, cash = frame["ret"].to_numpy(), frame["cash"].to_numpy()
    out = np.empty(len(frame))
    value, lev = 1.0, 1.0
    for t in range(len(frame)):
        if abs(target[t] - lev) > step:
            value *= 1.0 - abs(target[t] - lev) * cost_bps / 10_000.0
            lev = target[t]
        finance = (1.0 - lev) * cash[t] if lev <= 1.0 else -(lev - 1.0) * (cash[t] + borrow_spread / 252.0)
        value *= 1.0 + lev * ret[t] + finance
        out[t] = value
        if value <= 0:
            out[t:] = 0.0
            break
    return pd.Series(out, index=frame.index)


def rules(frame: pd.DataFrame, sma_days: int = 200) -> dict[str, tuple[pd.Series, float]]:
    """Wanted leverage per variant, plus the no-trade step."""
    close = frame["close"]
    above = (close > close.rolling(sma_days).mean()).astype(float)
    vol = frame["ret"].rolling(21).std() * math.sqrt(252)
    ones = pd.Series(1.0, index=frame.index)
    return {
        "1x": (ones, 0.0),
        "K1.5": (1.5 * ones, 0.0), "K2": (2.0 * ones, 0.0), "K3": (3.0 * ones, 0.0),
        "T1": (above, 0.0), "T2": (2.0 * above, 0.0), "T3": (3.0 * above, 0.0),
        "TB2": (1.0 + above, 0.0),
        "V2": ((0.20 / vol).clip(upper=2.0).fillna(1.0), 0.1),
        "V3": ((0.30 / vol).clip(upper=3.0).fillna(1.0), 0.1),
        "TV3": (((0.30 / vol).clip(upper=3.0).fillna(1.0)) * above, 0.1),
    }


def stats(equity: pd.Series) -> dict:
    out = {}
    for name, (a, b) in WINDOWS.items():
        e = equity.loc[a:b]
        if (e <= 0).any():
            out[name] = {"cagr": -1.0, "sharpe": float("nan"), "max_dd": -1.0, "vol": float("nan")}
            continue
        r = e.pct_change().dropna()
        years = (e.index[-1] - e.index[0]).days / 365.25
        out[name] = {"cagr": float((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1),
                     "sharpe": float(r.mean() / r.std() * math.sqrt(252)),
                     "max_dd": float((e / e.cummax() - 1).min()), "vol": float(r.std() * math.sqrt(252))}
    return out


def main() -> None:
    frame = load()
    out: dict = {"data": {"start": str(frame.index[0].date()), "end": str(frame.index[-1].date())}, "variants": {}}
    curves = {}
    wanted = rules(frame)
    for name, (w, step) in wanted.items():
        eq = simulate(frame, w, step=step)
        curves[name] = eq
        lev = w.shift(2).fillna(1.0)
        out["variants"][name] = {"windows": stats(eq), "avg_leverage": float(lev.mean()),
                                 "switches_per_year": float((lev.diff().abs() > 0.1).sum() / (len(frame) / 252))}
    base = out["variants"]["1x"]["windows"]
    for name, v in out["variants"].items():
        w = v["windows"]
        wins = [k for k in list(UNSEEN) + ["1999-2026"] if w[k]["cagr"] > base[k]["cagr"]]
        unseen_wins = [k for k in wins if k in UNSEEN]
        dd_ok = w["1928-2026"]["max_dd"] >= base["1928-2026"]["max_dd"]
        v["beats_1x_in"] = wins
        v["qualifies"] = bool(name != "1x" and len(wins) >= 3 and len(unseen_wins) >= 2 and dd_ok)
    qualifying = sorted((k for k, v in out["variants"].items() if v["qualifies"]),
                        key=lambda k: -out["variants"][k]["windows"]["1928-2026"]["cagr"])
    out["winner"] = qualifying[0] if qualifying else None
    out["qualifying"] = qualifying

    if out["winner"]:
        name = out["winner"]
        step = wanted[name][1]
        checks = {f"{d}-day average": simulate(frame, rules(frame, d)[name][0], step=step) for d in (100, 150, 250)}
        checks["borrow at cash + 3%"] = simulate(frame, wanted[name][0], borrow_spread=0.03, step=step)
        checks["20 bps per change"] = simulate(frame, wanted[name][0], cost_bps=20.0, step=step)
        checks["two days of delay"] = simulate(frame, wanted[name][0], delay=3, step=step)
        out["robustness"] = {k: stats(eq) for k, eq in checks.items()}

    (OUT / "century.json").write_text(json.dumps(out, indent=2))
    pd.DataFrame(curves).iloc[::5].to_csv(OUT / "century_equity_weekly.csv", float_format="%.6g")

    def pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    cols = list(WINDOWS)
    lines = ["# Leverage rules on the S&P 500 since 1928 (generated by examples/run_century_leverage.py)", "",
             "CAGR / worst drawdown per window. Borrowing at cash + 1.5%, 5 bps per change, one day of delay.", "",
             "| | " + " | ".join(cols) + " | Avg leverage | Switches/yr | Qualifies |", "|---" * (len(cols) + 4) + "|"]
    for name, v in out["variants"].items():
        cells = " | ".join(f"{pct(v['windows'][c]['cagr'])} / {pct(v['windows'][c]['max_dd'])}" for c in cols)
        lines.append(f"| {name} | {cells} | {v['avg_leverage']:.2f} | {v['switches_per_year']:.1f} | {'yes' if v['qualifies'] else 'no'} |")
    lines += ["", f"**Winner: {out['winner'] or 'none'}** (qualifying: {', '.join(qualifying) or 'none'})", ""]
    if out.get("robustness"):
        lines += [f"## Robustness of {out['winner']} (not selectable)", "", "| | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
        for k, w in out["robustness"].items():
            lines.append(f"| {k} | " + " | ".join(f"{pct(w[c]['cagr'])} / {pct(w[c]['max_dd'])}" for c in cols) + " |")
    (OUT / "century.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
