"""
Run the pre-registered Market Wizards (1989) variants: Schwartz (S1-S3), Seykota/Hite (K1-K3)
and Rogers (R1-R4). Rules are fixed in reports/market_wizards_1989/PREREGISTRATION.md.

Needs network for Yahoo Finance and FRED (cached under .cache/), and the S&P 500 panel from
examples/fetch_sp500_panel.py for K3 and R4.

    python examples/run_market_wizards_1989.py            # all variants
    python examples/run_market_wizards_1989.py S1 K1 R1   # a subset
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.weights_sim import period_stats, simulate_target_weights

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "market_wizards_1989"
OUT = ROOT / "reports" / "market_wizards_1989"
PANEL = ROOT / ".cache" / "sp500_panel" / "prices.parquet"
MEMBERSHIP = ROOT / "data" / "index_membership" / "sp500_membership_pit_panel.csv"
END = "2026-09-30"
HOLDOUT_START = "2020-01-01"
COUNTRIES = [
    "EWA", "EWC", "EWD", "EWG", "EWH", "EWI", "EWJ", "EWK", "EWL", "EWM", "EWN", "EWO",
    "EWP", "EWQ", "EWS", "EWU", "EWW", "EWZ", "EWY", "EWT", "EZA",
]


# ----------------------------------------------------------------------------- data

def tbill() -> pd.Series:
    path = CACHE / "DTB3.csv"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        raw = urllib.request.urlopen("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3").read()
        path.write_bytes(raw)
    frame = pd.read_csv(path, parse_dates=["observation_date"], na_values=".")
    return (frame.set_index("observation_date")["DTB3"] / 100.0).ffill()


def yahoo(symbols: list[str]) -> dict[str, pd.DataFrame]:
    path = CACHE / "yahoo_etfs.parquet"
    if not path.exists():
        import yfinance as yf

        CACHE.mkdir(parents=True, exist_ok=True)
        raw = yf.download(symbols, start="1993-01-01", end="2026-10-01", auto_adjust=True, progress=False)
        long = raw.stack(level=1, future_stack=True).reset_index()
        long.columns = [str(c).lower() for c in long.columns]
        long.to_parquet(path)
    long = pd.read_parquet(path)
    out = {}
    for field in ("open", "high", "low", "close"):
        out[field] = long.pivot(index="date", columns="ticker", values=field).sort_index().loc[:END]
    return out


def panel() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    frame = pd.read_parquet(PANEL, columns=["date", "ticker", "open", "high", "low", "close", "adj_close"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"] <= END]
    factor = frame["adj_close"] / frame["close"]
    frame["open"] *= factor
    frame["high"] *= factor
    frame["low"] *= factor
    frame["close"] = frame["adj_close"]
    out = {f: frame.pivot(index="date", columns="ticker", values=f).sort_index() for f in ("open", "high", "low", "close")}
    members = pd.read_csv(MEMBERSHIP, parse_dates=["effective_from", "effective_to"])
    member = pd.DataFrame(False, index=out["close"].index, columns=out["close"].columns)
    for row in members.itertuples():
        if row.symbol not in member.columns:
            continue
        hi = row.effective_to if pd.notna(row.effective_to) else member.index[-1]
        member.loc[row.effective_from : hi, row.symbol] = True
    return out, member


def sub(prices: dict[str, pd.DataFrame], cols: list[str]) -> dict[str, pd.DataFrame]:
    frame = {k: v[cols] for k, v in prices.items()}
    keep = frame["close"].notna().any(axis=1)
    return {k: v.loc[keep] for k, v in frame.items()}


# ----------------------------------------------------------------------------- signals

def ema(x: pd.DataFrame | pd.Series, lag: int):
    """Seykota/Schwartz exponential average: alpha = 2 / (lag + 1), seeded at the first value."""
    return x.ewm(span=lag, adjust=False).mean()


def atr(p: dict[str, pd.DataFrame], lag: int = 20) -> pd.DataFrame:
    prev = p["close"].shift(1)
    tr = np.maximum(p["high"] - p["low"], np.maximum((p["high"] - prev).abs(), (p["low"] - prev).abs()))
    return ema(tr, lag)


def on_change(weights: pd.DataFrame) -> pd.DataFrame:
    """Keep only the rows where the target changes (first row included)."""
    changed = weights.ne(weights.shift(1)).any(axis=1)
    changed.iloc[0] = True
    return weights.loc[changed]


def schwartz(p: dict[str, pd.DataFrame], symbol: str, short: bool) -> pd.DataFrame:
    c = p["close"][symbol].dropna()
    above = c > ema(c, 10)
    w = above.astype(float)
    if short:
        w = w.where(above, -1.0)
    return on_change(w.to_frame(symbol).iloc[10:])


def seykota_spy(p: dict[str, pd.DataFrame], cap: float | None) -> pd.DataFrame:
    c = p["close"]["SPY"].dropna()
    a = atr(sub(p, ["SPY"]))["SPY"].reindex(c.index)
    up = ema(c, 15) > ema(c, 150)
    up.iloc[:150] = np.nan  # no signal before the slow average has 150 bars
    w = pd.Series(np.nan, index=c.index)
    held = 0.0
    prev = None
    for ts, state in up.items():
        if pd.isna(state):
            continue
        if prev is not None and state and not prev:  # fast crosses above slow
            held = 0.10 / (5.0 * a[ts] / c[ts])
            if cap is not None:
                held = min(held, cap)
            w[ts] = held
        elif prev is not None and not state and prev:
            held = 0.0
            w[ts] = 0.0
        prev = bool(state)
    w = w.dropna()
    return w.to_frame("SPY")


def weekly_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = index.to_series()
    return pd.DatetimeIndex(s.groupby(s.dt.to_period("W")).first())


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = index.to_series()
    return pd.DatetimeIndex(s.groupby(s.dt.to_period("M")).last())


def seykota_hite_stocks(p: dict[str, pd.DataFrame], member: pd.DataFrame) -> pd.DataFrame:
    c = p["close"]
    bars = c.notna().cumsum()
    up = (ema(c, 15) > ema(c, 150)) & (bars >= 150) & c.notna()
    risk = 0.01 / (5.0 * atr(p) / c)
    dates = weekly_dates(c.index)
    w = risk.where(up & member).loc[dates].fillna(0.0)
    total = w.sum(axis=1)
    scale = np.where(total > 1.0, 1.0 / total.replace(0, np.nan), 1.0)
    return w.mul(np.nan_to_num(scale, nan=1.0), axis=0)


def rogers(close: pd.DataFrame, lookback: int, n: int, change_filter: bool, eligible: pd.DataFrame | None = None,
           equal_all: bool = False) -> pd.DataFrame:
    dates = month_ends(close.index)
    m = close.ffill(limit=5).loc[dates]
    ret = m / m.shift(lookback) - 1.0
    sma10 = m.rolling(10).mean()
    ok = ret.notna()
    if eligible is not None:
        ok &= eligible.reindex(index=dates, columns=m.columns).fillna(False)
    rows = []
    for ts in dates:
        cand = ret.loc[ts][ok.loc[ts]]
        w = pd.Series(0.0, index=m.columns)
        if equal_all:
            if len(cand):
                w[cand.index] = 1.0 / len(cand)
        elif len(cand) >= n:
            pick = cand.nsmallest(n).index
            if change_filter:
                pick = [s for s in pick if m.at[ts, s] > sma10.at[ts, s]]
            w[pick] = 1.0 / n
        else:
            continue
        rows.append(w.rename(ts))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- runner

def periods(equity: pd.Series, rf: pd.Series, first_signal: pd.Timestamp) -> dict[str, dict]:
    start = pd.Timestamp(f"{first_signal.year + 1}-01-01")
    e = equity[equity.index >= start]
    return {
        "in_sample": period_stats(e[e.index < HOLDOUT_START], rf),
        "holdout_2020_2026": period_stats(e[e.index >= HOLDOUT_START], rf),
        "full": period_stats(e, rf),
    }


def run(name: str, p: dict[str, pd.DataFrame], targets: pd.DataFrame, rf: pd.Series, spy: pd.Series, **kw) -> dict:
    equity, gross, turnover = simulate_target_weights(p["open"], p["high"], p["low"], p["close"], targets, rf, **kw)
    first = targets.index[0]
    equity = equity[equity.index > first]
    res = {
        "variant": name,
        "first_signal": str(first.date()),
        "periods": periods(equity, rf, first),
        "spy_same_dates": periods(spy.reindex(equity.index).ffill(), rf, first),
        "avg_gross_exposure": float(gross[gross.index > first].mean()),
        "annual_turnover": float(turnover[turnover.index > first].sum() / ((equity.index[-1] - first).days / 365.25)),
        "calendar_years": {str(y): float(g.iloc[-1] / g.iloc[0] - 1) for y, g in equity.groupby(equity.index.year)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}_summary.json").write_text(json.dumps(res, indent=2))
    pd.DataFrame({"equity": equity, "gross": gross.reindex(equity.index)}).to_csv(OUT / f"{name}_equity.csv")
    brief = {k: {m: round(v.get(m, float("nan")), 3) for m in ("cagr", "sharpe_rf0", "max_drawdown")} for k, v in res["periods"].items()}
    spyb = {k: {m: round(v.get(m, float("nan")), 3) for m in ("cagr", "sharpe_rf0", "max_drawdown")} for k, v in res["spy_same_dates"].items()}
    print(name, json.dumps(brief), "\n   SPY", json.dumps(spyb), f"gross={res['avg_gross_exposure']:.2f}", flush=True)
    return res


def main(selected: list[str]) -> None:
    want = lambda v: not selected or v in selected  # noqa: E731
    rf = tbill()
    etf = yahoo(["SPY", "QQQ"] + COUNTRIES)
    spy = etf["close"]["SPY"].dropna()

    if want("S1"):
        run("S1", sub(etf, ["SPY"]), schwartz(etf, "SPY", short=False), rf, spy)
    if want("S2"):
        run("S2", sub(etf, ["SPY"]), schwartz(etf, "SPY", short=True), rf, spy)
    if want("S3"):
        run("S3", sub(etf, ["QQQ"]), schwartz(etf, "QQQ", short=False), rf, spy)
    if want("K1"):
        run("K1", sub(etf, ["SPY"]), seykota_spy(etf, cap=None), rf, spy, skid=0.5)
    if want("K2"):
        run("K2", sub(etf, ["SPY"]), seykota_spy(etf, cap=1.0), rf, spy, skid=0.5)

    funds = ["SPY"] + COUNTRIES
    fp = sub(etf, funds)
    if want("R1"):
        run("R1", fp, rogers(fp["close"], 60, 4, True), rf, spy)
    if want("R2"):
        run("R2", fp, rogers(fp["close"], 60, 4, False), rf, spy)
    if want("R3"):
        run("R3", fp, rogers(fp["close"], 60, 0, False, equal_all=True), rf, spy)

    if want("K3") or want("R4"):
        sp, member = panel()
        if want("K3"):
            run("K3", sp, seykota_hite_stocks(sp, member), rf, spy)
        if want("R4"):
            run("R4", sp, rogers(sp["close"], 36, 20, True, eligible=member), rf, spy)


if __name__ == "__main__":
    main(sys.argv[1:])
