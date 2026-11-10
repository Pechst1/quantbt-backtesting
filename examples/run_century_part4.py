"""
Round 2, part 4 (exploratory, seen data): the Part 3 winner with and without a Cook-panic
override, next to 1x and R1, all in the Part 3 framework. Writes century_part4.json.
Run after run_best_strategy.py (needs cook_state.csv) and run_century_leverage.py.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_century_leverage as century

WINDOWS = {"1999-2009": ("1999-01-01", "2009-12-31"), "2010-2019": ("2010-01-01", "2019-12-31"),
           "2020-2026": ("2020-01-01", "2026-09-30"), "2010-2026": ("2010-01-01", "2026-09-30"),
           "1999-2026": ("1999-01-01", "2026-09-30")}


def main() -> None:
    frame = century.load()
    cook = pd.read_csv(century.OUT / "cook_state.csv", index_col=0, parse_dates=True)["buy"]
    cook = cook.astype(bool).reindex(frame.index).fillna(False).astype(bool)
    wanted = century.rules(frame)
    tv3 = wanted["TV3"][0]
    variants = {
        "1x": (wanted["1x"][0], 0.0),
        "R1": (1.0 + 0.5 * cook.astype(float), 0.0),
        "T2": (wanted["T2"][0], 0.0),
        "TV3": (tv3, 0.1),
        "TC": (tv3.where(~cook, tv3.clip(lower=1.5)), 0.1),
    }
    out = {}
    curves = {}
    for name, (want, step) in variants.items():
        eq = century.simulate(frame, want, step=step)
        curves[name] = eq
        row = {}
        for win, (a, b) in WINDOWS.items():
            e = eq.loc[a:b]
            r = e.pct_change().dropna()
            years = (e.index[-1] - e.index[0]).days / 365.25
            row[win] = {"cagr": float((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1),
                        "sharpe": float(r.mean() / r.std() * math.sqrt(252)),
                        "max_dd": float((e / e.cummax() - 1).min())}
        out[name] = row
        print(name, {k: (round(v["cagr"] * 100, 1), round(v["sharpe"], 2), round(v["max_dd"] * 100)) for k, v in row.items()})
    # Descriptive checks on TC (seen data; nothing is selected from these)
    def tc(days: int = 200, floor: float = 1.5) -> pd.Series:
        base = century.rules(frame, days)["TV3"][0]
        return base.where(~cook, base.clip(lower=floor))

    def brief(eq: pd.Series) -> dict:
        res = {}
        for win in ("1999-2009", "2010-2026", "1999-2026"):
            e = eq.loc[WINDOWS[win][0]:WINDOWS[win][1]]
            years = (e.index[-1] - e.index[0]).days / 365.25
            res[win] = {"cagr": float((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1), "max_dd": float((e / e.cummax() - 1).min())}
        return res

    checks = {
        "150-day average": century.simulate(frame, tc(150), step=0.1),
        "250-day average": century.simulate(frame, tc(250), step=0.1),
        "panic floor 1.0x": century.simulate(frame, tc(floor=1.0), step=0.1),
        "panic floor 2.0x": century.simulate(frame, tc(floor=2.0), step=0.1),
        "borrow at cash + 3%": century.simulate(frame, tc(), borrow_spread=0.03, step=0.1),
        "20 bps per change": century.simulate(frame, tc(), cost_bps=20.0, step=0.1),
        "two days of delay": century.simulate(frame, tc(), delay=3, step=0.1),
        "leverage cap 2x, target 20%": century.simulate(
            frame, (century.rules(frame)["V2"][0] * (frame["close"] > frame["close"].rolling(200).mean())).where(
                ~cook, (century.rules(frame)["V2"][0] * (frame["close"] > frame["close"].rolling(200).mean())).clip(lower=1.5)), step=0.1),
    }
    out["tc_checks"] = {k: brief(eq) for k, eq in checks.items()}
    for k, v in out["tc_checks"].items():
        print("  TC", k, {w: (round(x["cagr"] * 100, 1), round(x["max_dd"] * 100)) for w, x in v.items()})
    lev = variants["TC"][0].loc["1999-01-01":]
    out["tc_exposure"] = {"avg_leverage": float(lev.mean()), "share_in_tbills": float((lev == 0).mean()),
                          "share_above_2x": float((lev > 2.0).mean()),
                          "changes_per_year": float((lev.diff().abs() > 0.1).sum() / (len(lev) / 252))}
    print(out["tc_exposure"])
    calendar = pd.DataFrame(curves).loc["1998-12-31":].resample("YE").last().pct_change().dropna()
    out["calendar"] = {str(k.year): {c: float(v) for c, v in row.items()} for k, row in calendar.iterrows()}
    out["wanted_leverage_latest"] = {"date": str(frame.index[-1].date()), "TV3": float(tv3.iloc[-1])}
    (century.OUT / "century_part4.json").write_text(json.dumps(out, indent=2))
    print((calendar.loc["2007":] * 100).round(0).T.to_string())
    print(out["wanted_leverage_latest"])


if __name__ == "__main__":
    main()
