"""
Daily paper trade of TC (reports/best_strategy/README.md, round 2):

  1. S&P 500 above its 200-day average: leverage = 30% / 21-day annualised volatility, capped at 3x.
  2. Below it: T-bills (leverage 0).
  3. Mark Cook's breadth panic state on: at least 1.5x.

Mechanics are the backtest's: a reading on the close of day t sets the position that earns
the return of day t+2; daily-reset leverage on SPY's total return; borrowed money pays the
T-bill rate + 1.5%, idle money earns the T-bill rate; 5 bps on each change of leverage, made
only when the wanted leverage moves by more than 0.1.

State lives in --dir (default /mnt/project-files/paper_trade/):
  cook_history.csv  daily breadth, 21-day sum and panic state (seeded from the backtest)
  ledger.csv        one row per trading day since the start
  status.json       the latest reading, the position in force and any warnings

Only needs pandas and Yahoo's chart API. Safe to run more than once a day: it only appends
trading days that are complete and not yet in the ledger.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

START_EQUITY = 25_000.0
TARGET_VOL, MAX_LEV, PANIC_FLOOR, SMA_DAYS, VOL_DAYS = 0.30, 3.0, 1.5, 200, 21
STEP, COST_BPS, BORROW_SPREAD, DELAY = 0.1, 5.0, 0.015, 2
QUIET_BAND, SUM_WINDOW, LOW_PCT = 400 / 3000, 21, 5.0
MIN_BREADTH_NAMES = 400
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"


def yahoo(symbol: str, span: str = "2y", adjusted: bool = False, retries: int = 3) -> pd.Series:
    url = CHART.format(symbol=urllib.parse.quote(symbol), range=span)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read())["chart"]["result"][0]
            index = pd.to_datetime(result["timestamp"], unit="s").normalize()
            quote = result["indicators"]["quote"][0]["close"]
            adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
            series = pd.Series(adj if (adjusted and adj is not None) else quote, index=index, dtype=float).dropna()
            return series[~series.index.duplicated(keep="last")]
        except (OSError, KeyError, TypeError, ValueError) as exc:  # network hiccup or unknown symbol
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Yahoo failed for {symbol}: {last}")


def current_members(membership_csv: Path) -> list[str]:
    spells = pd.read_csv(membership_csv)
    return sorted(spells.loc[spells["effective_to"].isna(), "symbol"].unique())


def breadth_from_yahoo(members: list[str], span: str = "3mo") -> tuple[pd.Series, pd.Series]:
    """Daily (advancers - decliners) / names with a close on both days, and that count."""
    def one(symbol: str) -> pd.Series | None:
        try:
            return yahoo(symbol, span).rename(symbol)
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = [s for s in pool.map(one, members) if s is not None]
    close = pd.concat(frames, axis=1).sort_index()
    diff = close.diff().where(close.notna() & close.shift(1).notna())
    n = diff.notna().sum(axis=1)
    b = ((diff > 0).sum(axis=1) - (diff < 0).sum(axis=1)) / n.where(n > 0)
    return b, n


def extend_cook(history: pd.DataFrame, breadth: pd.Series, counts: pd.Series, last_complete: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    """Append new days to the Cook history with the backtest's state machine (unchanged rules)."""
    warnings: list[str] = []
    overlap = history["breadth"].reindex(breadth.index).dropna().iloc[-15:]
    if len(overlap) >= 5:
        gap = float((breadth.reindex(overlap.index) - overlap).abs().mean())
        if gap > 0.03:
            warnings.append(f"Yahoo breadth differs from the backtest panel by {gap:.3f} a day on the overlap")
    new_days = [d for d in breadth.index if d > history.index[-1] and d <= last_complete]
    values = history["cum_breadth"].to_numpy().tolist()
    on = bool(history["buy"].iloc[-1])
    rows = []
    xs = history["breadth"].where(history["breadth"].abs() > QUIET_BAND, 0.0).to_numpy().tolist()
    for day in new_days:
        if counts.loc[day] < MIN_BREADTH_NAMES or not np.isfinite(breadth.loc[day]):
            warnings.append(f"breadth for {day.date()} has only {int(counts.loc[day])} names; day skipped for the panic state")
            break
        b = float(breadth.loc[day])
        xs.append(b if abs(b) > QUIET_BAND else 0.0)
        c = float(sum(xs[-SUM_WINDOW:]))
        low, mid = np.percentile(values, [LOW_PCT, 50.0])
        if on and c >= mid:
            on = False
        if not on and c < low:
            on = True
        values.append(c)
        rows.append({"date": day, "breadth": b, "cum_breadth": c, "buy": on, "ready": True})
    if rows:
        history = pd.concat([history, pd.DataFrame(rows).set_index("date")])
    return history, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="/mnt/project-files/paper_trade/")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--seed-cook", default="reports/best_strategy/cook_state.csv")
    parser.add_argument("--start", default=None, help="First ledger day (its close is the starting point). Default: last complete day.")
    args = parser.parse_args()
    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    # ---- market data
    index = yahoo("^GSPC", "5y")
    spy = yahoo("SPY", "5y", adjusted=True)
    irx = yahoo("^IRX", "5y")
    now_ny = pd.Timestamp.now(tz="America/New_York")
    today = now_ny.normalize().tz_localize(None)
    # A bar dated today is only complete after the close (16:00 New York, plus settling time).
    if index.index[-1] == today and now_ny.hour < 17:
        index = index.iloc[:-1]
    last_complete = index.index[-1]
    spy = spy.loc[:last_complete]
    if spy.index[-1] != last_complete:
        warnings.append(f"SPY ends {spy.index[-1].date()} but the index ends {last_complete.date()}")
    age = (today - last_complete).days
    if age > 4:
        warnings.append(f"latest complete bar is {age} days old")

    # ---- Cook panic state
    cook_path = root / "cook_history.csv"
    seed = cook_path if cook_path.exists() else Path(args.seed_cook)
    history = pd.read_csv(seed, index_col=0, parse_dates=True)
    history.index.name = "date"
    history["buy"] = history["buy"].astype(str).str.lower().eq("true")
    if history.index[-1] < last_complete:
        breadth, counts = breadth_from_yahoo(current_members(Path(args.membership)))
        history, cook_warnings = extend_cook(history, breadth, counts, last_complete)
        warnings += cook_warnings
    history.to_csv(cook_path, float_format="%.5f")
    cook = history["buy"].reindex(index.index)
    if cook.loc[last_complete:].isna().any() or pd.isna(cook.iloc[-1]):
        warnings.append("panic state is missing for the latest day; treated as off")
    cook = cook.fillna(False).astype(bool)

    # ---- readings and wanted leverage on every close
    sma = index.rolling(SMA_DAYS).mean()
    vol = index.pct_change().rolling(VOL_DAYS).std() * math.sqrt(252)
    trend = (TARGET_VOL / vol).clip(upper=MAX_LEV).where(index > sma, 0.0)
    wanted = trend.where(~cook, trend.clip(lower=PANIC_FLOOR))
    readings = pd.DataFrame({"index_close": index, "sma200": sma, "vol21": vol, "panic": cook, "wanted": wanted})

    # ---- ledger
    ledger_path = root / "ledger.csv"
    if ledger_path.exists():
        ledger = pd.read_csv(ledger_path, index_col=0, parse_dates=True)
    else:
        start = pd.Timestamp(args.start) if args.start else last_complete
        start = index.index[index.index.searchsorted(start, side="right") - 1]
        first_lev = float(wanted.shift(DELAY).loc[start])
        ledger = pd.DataFrame([{**readings.loc[start].to_dict(), "leverage": first_lev, "strategy_return": 0.0,
                                "spy_return": 0.0, "equity": START_EQUITY, "spy_equity": START_EQUITY, "note": "start"}],
                              index=pd.DatetimeIndex([start], name="date"))
    spy_ret = spy.pct_change()
    cash = (irx.reindex(index.index.union(irx.index)).ffill().reindex(index.index).shift(1) / 100.0 / 252.0).clip(lower=0.0)
    target = wanted.shift(DELAY)
    rows = []
    equity, spy_equity, lev = float(ledger["equity"].iloc[-1]), float(ledger["spy_equity"].iloc[-1]), float(ledger["leverage"].iloc[-1])
    for day in index.index[index.index > ledger.index[-1]]:
        if day not in spy_ret.index or pd.isna(spy_ret.loc[day]) or pd.isna(target.loc[day]):
            warnings.append(f"no complete data for {day.date()}; ledger stops before it")
            break
        note = ""
        want = float(target.loc[day])
        if abs(want - lev) > STEP:
            equity *= 1.0 - abs(want - lev) * COST_BPS / 10_000.0
            note = f"leverage {lev:.2f} -> {want:.2f}"
            lev = want
        c = float(cash.loc[day]) if pd.notna(cash.loc[day]) else 0.0
        finance = (1.0 - lev) * c if lev <= 1.0 else -(lev - 1.0) * (c + BORROW_SPREAD / 252.0)
        r = lev * float(spy_ret.loc[day]) + finance
        equity *= 1.0 + r
        spy_equity *= 1.0 + float(spy_ret.loc[day])
        rows.append({**readings.loc[day].to_dict(), "leverage": lev, "strategy_return": r, "spy_return": float(spy_ret.loc[day]),
                     "equity": equity, "spy_equity": spy_equity, "note": note})
        ledger.loc[day] = rows[-1]
    ledger.index.name = "date"
    ledger.to_csv(ledger_path, float_format="%.6f")

    # ---- status
    latest = readings.loc[last_complete]
    # A reading on close t sets the position for day t+2, so the next two days are already decided.
    def step_to(current: float, want: float) -> float:
        return want if abs(want - current) > STEP else current

    next_day = step_to(lev, float(wanted.iloc[-2]))
    day_after = step_to(next_day, float(wanted.iloc[-1]))
    status = {
        "run_at_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M"),
        "last_complete_day": str(last_complete.date()),
        "reading": {"index_close": round(float(latest["index_close"]), 2), "sma200": round(float(latest["sma200"]), 2),
                    "above_sma": bool(latest["index_close"] > latest["sma200"]), "vol21": round(float(latest["vol21"]), 4),
                    "panic": bool(latest["panic"]), "wanted_leverage": round(float(latest["wanted"]), 2)},
        "position": {"leverage_last_ledger_day": round(lev, 2), "leverage_next_trading_day": round(next_day, 2),
                     "leverage_day_after": round(day_after, 2)},
        "account": {"start": str(ledger.index[0].date()), "days": len(ledger) - 1, "equity": round(equity, 2),
                    "spy_equity": round(spy_equity, 2),
                    "return": round(equity / START_EQUITY - 1.0, 4), "spy_return": round(spy_equity / START_EQUITY - 1.0, 4),
                    "max_drawdown": round(float((ledger["equity"] / ledger["equity"].cummax() - 1.0).min()), 4)},
        "new_days": [str(d.date()) for d in ledger.index[len(ledger) - len(rows):]] if rows else [],
        "changes": [f"{d.date()}: {n}" for d, n in ledger["note"].iloc[len(ledger) - len(rows):].items() if isinstance(n, str) and n]
        if rows else [],
        "warnings": warnings,
    }
    (root / "status.json").write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
