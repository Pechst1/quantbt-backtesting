"""
Run the pre-registered Kullamägi setups (reports/next_gen_market_wizards/PREREGISTRATION.md).

SPX variants need examples/fetch_sp500_panel.py first; US variants need
examples/build_massive_us_panel.py (all US common stocks from Massive, 2021-10 onward).
Idle cash earns the 3-month T-bill rate (FRED DTB3); Sharpe is in excess of it.
"""

from __future__ import annotations

import argparse
import bisect
import json
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.data.panel import ParquetPanelSource
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.kullamaggie import KullamaggieStrategy

VARIANTS = {
    "KB-A10": dict(universe="SPX", setup="breakout", min_adr=0.04, trail=10),
    "KB-A20": dict(universe="SPX", setup="breakout", min_adr=0.04, trail=20),
    "KB-C10": dict(universe="SPX", setup="breakout", min_adr=None, trail=10),
    "EP-10": dict(universe="SPX", setup="ep", min_adr=None, trail=10),
    "EP-20": dict(universe="SPX", setup="ep", min_adr=None, trail=20),
    "US-KB-A10": dict(universe="US", setup="breakout", min_adr=0.04, trail=10),
    "US-EP-10": dict(universe="US", setup="ep", min_adr=None, trail=10),
}
SPX_PERIODS = (("full", "1999-07-01", "2026-12-31"), ("in_sample_1999_2019", "1999-07-01", "2019-12-31"), ("holdout_2020_2026", "2020-01-01", "2026-12-31"))
US_PERIODS = (("us_2022_2026", "2022-04-01", "2026-12-31"),)
DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"


class IntervalMembership:
    """Fast is_member lookup over the interval CSV written by fetch_sp500_panel.py."""

    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(path, parse_dates=["effective_from", "effective_to"])
        self._spells: dict[str, list[tuple[date, date]]] = {}
        for row in frame.itertuples():
            end = date.max if pd.isna(row.effective_to) else row.effective_to.date()
            self._spells.setdefault(row.symbol, []).append((row.effective_from.date(), end))
        for spells in self._spells.values():
            spells.sort()

    @property
    def symbols(self) -> list[str]:
        return sorted(self._spells)

    def is_member(self, symbol: str, as_of: date) -> bool:
        spells = self._spells.get(symbol)
        if not spells:
            return False
        k = bisect.bisect_right(spells, (as_of, date.max)) - 1
        return k >= 0 and spells[k][0] <= as_of <= spells[k][1]


def load_tbill(cache: Path) -> pd.Series:
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(DTB3_URL, cache)
    frame = pd.read_csv(cache, parse_dates=["observation_date"], na_values=".")
    return (frame.set_index("observation_date")["DTB3"].ffill() / 100.0).sort_index()


def period_stats(equity: pd.Series, rf_daily: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    rets = equity.pct_change().dropna()
    excess = rets - rf_daily.reindex(rets.index).fillna(0.0)
    dd = equity / equity.cummax() - 1.0
    return {
        "cagr": float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1),
        "ann_vol": float(rets.std() * np.sqrt(252)),
        "sharpe": float(excess.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else float("nan"),
        "max_drawdown": float(dd.min()),
    }


def trade_stats(trades) -> dict[str, float]:
    rets = np.array([t.exit_price / t.entry_price - 1.0 for t in trades if t.entry_price > 0])
    holds = np.array([(t.exit_timestamp - t.entry_timestamp).days for t in trades if t.entry_timestamp and t.exit_timestamp])
    return {
        "count": len(trades),
        "win_rate": float((rets > 0).mean()) if len(rets) else 0.0,
        "avg_return": float(rets.mean()) if len(rets) else 0.0,
        "avg_win": float(rets[rets > 0].mean()) if (rets > 0).any() else 0.0,
        "avg_loss": float(rets[rets <= 0].mean()) if (rets <= 0).any() else 0.0,
        "median_hold_calendar_days": float(np.median(holds)) if len(holds) else 0.0,
    }


def run_variant(name: str, args: argparse.Namespace, tbill: pd.Series) -> dict:
    spec = VARIANTS[name]
    if spec["universe"] == "SPX":
        membership = IntervalMembership(Path(args.membership))
        source = ParquetPanelSource(args.prices, symbols=sorted(set(membership.symbols) | {"SPY", "QQQ"}))
        symbols = sorted((set(membership.symbols) & set(source.symbols)) | {"SPY", "QQQ"})
        tradable = set(membership.symbols)
        start, report_start, periods_def = args.start, "1999-07-01", SPX_PERIODS
        extra = dict(is_member=membership.is_member)
    else:
        source = ParquetPanelSource(args.us_prices)
        symbols = source.symbols
        tradable = set(symbols) - {"SPY", "QQQ"}
        start, report_start, periods_def = "2021-10-04", "2022-04-01", US_PERIODS
        extra = dict(min_price=5.0, min_dollar_volume=5_000_000.0)
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(
            symbols=symbols,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(args.end),
            cache_dir=Path(".cache/market_data"),
            required_symbols={"SPY", "QQQ"},
        ),
        source=source,
    )
    strategy = KullamaggieStrategy(
        symbols=data.active_symbols,
        setup=spec["setup"],
        tradable=tradable,
        min_adr=spec["min_adr"],
        trail_sma=spec["trail"],
        **extra,
    )

    def rate(as_of: date) -> float:
        k = tbill.index.searchsorted(pd.Timestamp(as_of), side="right") - 1
        return float(tbill.iloc[k]) if k >= 0 else 0.0

    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0, cash_interest_rate=rate)
    engine = BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(ExecutionConfig()))
    result = engine.run(run_name=name)

    equity = result.report.equity_curve
    equity = equity[equity.index >= pd.Timestamp(report_start)]
    spy = data._frames["SPY"]["close"].reindex(equity.index)
    rf_daily = (tbill.reindex(equity.index, method="ffill") / 252.0).fillna(0.0)
    exposure = pd.Series([s.gross_exposure / s.equity for s in portfolio.history], index=[s.timestamp for s in portfolio.history])
    trades = portfolio.closed_trades

    def in_period(t, lo, hi):
        return t.entry_timestamp is not None and pd.Timestamp(lo) <= pd.Timestamp(t.entry_timestamp) <= pd.Timestamp(hi)

    periods = {}
    for label, lo, hi in periods_def:
        mask = (equity.index >= lo) & (equity.index <= hi)
        periods[label] = {
            "strategy": period_stats(equity[mask], rf_daily),
            "spy": period_stats(spy[mask], rf_daily),
            "avg_exposure": float(exposure.reindex(equity.index[mask]).mean()),
            "trades": trade_stats([t for t in trades if in_period(t, lo, hi)]),
        }
    summary = {
        "variant": name,
        "spec": spec,
        "symbols": len(symbols),
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "periods": periods,
        "setups_seen": strategy.setups_seen,
        "orders_placed": strategy.setups_placed,
        "exit_reasons": dict(Counter(str(t.metadata.get("exit_reason", "?")) for t in trades)),
        "engine": {"fills": result.fills, "margin_calls": result.margin_calls, "cash_interest_earned": result.cash_interest_earned, "margin_interest_paid": result.margin_interest_paid},
    }
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    pd.DataFrame({"strategy": equity, "spy": spy / spy.iloc[0] * equity.iloc[0]}).to_csv(out / f"{name}_equity.csv", float_format="%.2f")
    pd.DataFrame(
        [
            {"symbol": t.symbol, "entry": t.entry_timestamp, "exit": t.exit_timestamp, "entry_price": round(t.entry_price, 4), "exit_price": round(t.exit_price, 4), "quantity": t.quantity, "pnl": round(t.pnl, 2), "reason": t.metadata.get("exit_reason")}
            for t in trades
        ]
    ).to_csv(out / f"{name}_trades.csv", index=False)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--us-prices", default=".cache/massive_us/prices.parquet")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--start", default="1998-07-01", help="SPX data start (one year of indicator warm-up).")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output-dir", default="reports/next_gen_market_wizards")
    args = parser.parse_args()

    tbill = load_tbill(Path(".cache/fred/DTB3.csv"))
    for name in args.variants.split(","):
        summary = run_variant(name, args, tbill)
        print(json.dumps({"variant": name, **{k: {kk: summary["periods"][k][kk] for kk in ("strategy", "spy", "avg_exposure")} for k in summary["periods"]}, "trades": {k: v["trades"] for k, v in summary["periods"].items()}}, indent=1), flush=True)


if __name__ == "__main__":
    main()
