"""
Run the pre-registered Linda Raschke setups (reports/new_market_wizards/PREREGISTRATION.md).

Run examples/fetch_sp500_panel.py first. Variants:
  TS-SPX, TS-SPX-200, HG-SPX   on point-in-time S&P 500 members (10 slots)
  TS-ETF, TS-ETF-200, HG-ETF   on 18 index, sector, bond and gold ETFs (5 slots)
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
from quantbt.strategies.raschke import HolyGrailStrategy, TurtleSoupPlusOneStrategy

ETFS = ["SPY", "QQQ", "IWM", "MDY", "DIA", "EFA", "EEM", "TLT", "GLD", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
VARIANTS = {
    "TS-SPX": dict(setup="TS", universe="SPX", slots=10, trend=None),
    "TS-SPX-200": dict(setup="TS", universe="SPX", slots=10, trend=200),
    "HG-SPX": dict(setup="HG", universe="SPX", slots=10, trend=None),
    "TS-ETF": dict(setup="TS", universe="ETF", slots=5, trend=None),
    "TS-ETF-200": dict(setup="TS", universe="ETF", slots=5, trend=200),
    "HG-ETF": dict(setup="HG", universe="ETF", slots=5, trend=None),
}
PERIODS = (("full", "1998-01-01", "2026-12-31"), ("in_sample_1998_2019", "1998-01-01", "2019-12-31"), ("holdout_2020_2026", "2020-01-01", "2026-12-31"))
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


def run_variant(name: str, args: argparse.Namespace, source: ParquetPanelSource, membership: IntervalMembership, tbill: pd.Series) -> dict:
    spec = VARIANTS[name]
    if spec["universe"] == "SPX":
        symbols = sorted((set(membership.symbols) & set(source.symbols)) | {"SPY"})
        is_member = membership.is_member
    else:
        symbols = [s for s in ETFS if s in set(source.symbols)]
        is_member = None
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(
            symbols=symbols,
            start=datetime.fromisoformat(args.start),
            end=datetime.fromisoformat(args.end),
            cache_dir=Path(".cache/market_data"),
            required_symbols={"SPY"},
        ),
        source=source,
    )
    cls = TurtleSoupPlusOneStrategy if spec["setup"] == "TS" else HolyGrailStrategy
    strategy = cls(symbols=data.active_symbols, is_member=is_member, max_positions=spec["slots"], trend_filter_days=spec["trend"])

    def rate(as_of: date) -> float:
        k = tbill.index.searchsorted(pd.Timestamp(as_of), side="right") - 1
        return float(tbill.iloc[k]) if k >= 0 else 0.0

    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0, cash_interest_rate=rate)
    engine = BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(ExecutionConfig()))
    result = engine.run(run_name=name)

    equity = result.report.equity_curve
    equity = equity[equity.index >= pd.Timestamp(args.report_start)]
    spy = data._frames["SPY"]["close"].reindex(equity.index)
    rf_daily = (tbill.reindex(equity.index, method="ffill") / 252.0).fillna(0.0)
    exposure = pd.Series([s.gross_exposure / s.equity for s in portfolio.history], index=[s.timestamp for s in portfolio.history])
    trades = portfolio.closed_trades

    def in_period(t, lo, hi):
        return t.entry_timestamp is not None and pd.Timestamp(lo) <= pd.Timestamp(t.entry_timestamp) <= pd.Timestamp(hi)

    periods = {}
    for label, lo, hi in PERIODS:
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
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--start", default="1997-01-01", help="Data start (one year of indicator warm-up).")
    parser.add_argument("--report-start", default="1998-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output-dir", default="reports/new_market_wizards")
    args = parser.parse_args()

    membership = IntervalMembership(Path(args.membership))
    source = ParquetPanelSource(args.prices, symbols=sorted(set(membership.symbols) | set(ETFS)))
    tbill = load_tbill(Path(".cache/fred/DTB3.csv"))
    for name in args.variants.split(","):
        summary = run_variant(name, args, source, membership, tbill)
        print(json.dumps({"variant": name, **{k: {kk: summary["periods"][k][kk] for kk in ("strategy", "spy", "avg_exposure")} for k in summary["periods"]}, "trades_full": summary["periods"]["full"]["trades"]}, indent=1), flush=True)


if __name__ == "__main__":
    main()
