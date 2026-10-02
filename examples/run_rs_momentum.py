"""
Backtest the pre-registered relative-strength leaders variants on point-in-time S&P 500 members.

Run examples/fetch_sp500_panel.py first. Rules and the variant list are fixed in
reports/rs_momentum/PREREGISTRATION.md; nothing here is fitted to the results.

    python examples/run_rs_momentum.py --variant C
    python examples/run_rs_momentum.py --benchmarks
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_minervini_trend_template import IntervalMembership, period_stats  # noqa: E402

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.data.panel import ParquetPanelSource
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.rs_momentum import RelativeStrengthLeadersStrategy

VARIANTS = {
    "A": dict(ranking="mom_12_1", top_n=10, market_symbol=None, stop_loss_pct=None),
    "B": dict(ranking="mom_12_1", top_n=10, market_symbol="SPY", stop_loss_pct=None),
    "C": dict(ranking="mom_12_1", top_n=10, market_symbol="SPY", stop_loss_pct=0.08),
    "D": dict(ranking="mom_12_1", top_n=5, market_symbol="SPY", stop_loss_pct=0.08),
    "E": dict(ranking="mom_12_1", top_n=20, market_symbol="SPY", stop_loss_pct=0.08),
    "F": dict(ranking="ibd_rs", top_n=10, market_symbol="SPY", stop_loss_pct=0.08),
}
PERIODS = (("in_sample_1999_2019", "1999-01-01", "2019-12-31"), ("holdout_2020_2026", "2020-01-01", "2026-12-31"), ("full_1999_2026", "1999-01-01", "2026-12-31"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(VARIANTS))
    parser.add_argument("--benchmarks", action="store_true", help="Write SPY and equal-weight member benchmarks.")
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--start", default="1998-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--output-dir", default="reports/rs_momentum")
    return parser.parse_args()


def by_period(equity: pd.Series) -> dict[str, dict[str, float]]:
    return {name: period_stats(equity[(equity.index >= lo) & (equity.index <= hi)]) for name, lo, hi in PERIODS}


def benchmarks(args: argparse.Namespace, membership: IntervalMembership) -> None:
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    closes = pd.DataFrame({s: f["adj_close"] for s, f in source._frames.items()}).sort_index()
    closes = closes.loc[args.start : args.end]
    spy = closes.pop("SPY")
    # Equal weight across members on each month's first trading day, held for the month.
    rets = closes.pct_change(fill_method=None)
    month_starts = closes.index.to_series().groupby(closes.index.to_period("M")).first()
    weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    for ts in month_starts:
        alive = closes.loc[ts].notna()
        members = np.array([membership.is_member(s, ts.date()) for s in closes.columns])
        w = (alive.values & members).astype(float)
        weights.loc[ts] = w / w.sum() if w.sum() else w
    # Signal at the first day's close, so the weights start earning from the next bar.
    weights = weights.ffill().shift(1)
    ew = (1.0 + (weights * rets.fillna(0.0)).sum(axis=1)).cumprod()
    out = {
        "SPY": by_period(spy.dropna()),
        "EW_members": by_period(ew[ew.index >= "1999-01-01"]),
    }
    Path(args.output_dir, "benchmarks_summary.json").write_text(json.dumps(out, indent=2))
    pd.DataFrame({"spy": spy, "ew_members": ew}).to_csv(Path(args.output_dir, "benchmarks_equity.csv"))
    print(json.dumps(out, indent=2))


def run_variant(args: argparse.Namespace, membership: IntervalMembership) -> None:
    rules = VARIANTS[args.variant]
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    symbols = [s for s in membership.symbols if s in set(source.symbols)] + ["SPY"]
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
    strategy = RelativeStrengthLeadersStrategy(symbols=data.active_symbols, is_member=membership.is_member, **rules)
    portfolio = Portfolio(initial_cash=args.initial_cash, leverage=1.0)
    engine = BacktestEngine(
        data_handler=data,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=SimulatedExecutionHandler(ExecutionConfig()),
    )
    run_name = f"rs_leaders_{args.variant}"
    result = engine.run(run_name=run_name)

    equity = result.report.equity_curve
    equity = equity[equity.index >= "1999-01-01"]
    exposure = pd.Series([s.gross_exposure / s.equity for s in portfolio.history], index=[s.timestamp for s in portfolio.history])
    trades = portfolio.closed_trades
    rets = np.array([t.exit_price / t.entry_price - 1.0 for t in trades if t.entry_price > 0])
    holds = np.array([(t.exit_timestamp - t.entry_timestamp).days for t in trades if t.entry_timestamp and t.exit_timestamp])
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    summary = {
        "variant": args.variant,
        "rules": rules,
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "periods": by_period(equity),
        "calendar_years": {
            str(y): float(g.iloc[-1] / g.iloc[0] - 1.0)
            for y, g in equity.groupby(equity.index.year)
        },
        "avg_gross_exposure": float(exposure[exposure.index >= equity.index[0]].mean()),
        "rebalances": strategy.rebalances,
        "filter_off_months": strategy.filter_off_months,
        "dead_positions": sorted(strategy._dead),
        "trades": {
            "count": len(trades),
            "per_year": len(trades) / years,
            "win_rate": float((rets > 0).mean()) if len(rets) else 0.0,
            "avg_return": float(rets.mean()) if len(rets) else 0.0,
            "avg_win": float(rets[rets > 0].mean()) if (rets > 0).any() else 0.0,
            "avg_loss": float(rets[rets <= 0].mean()) if (rets <= 0).any() else 0.0,
            "median_hold_days": float(np.median(holds)) if len(holds) else 0.0,
            "exit_reasons": dict(Counter(str(t.metadata.get("exit_reason", "?")) for t in trades)),
        },
        "engine": {"fills": result.fills, "rejected_orders": result.rejected_orders, "risk_rejections": result.risk_rejections},
        "current_leaders": strategy.last_ranking,
        "open_positions": {s: int(p.quantity) for s, p in portfolio.positions.items() if p.quantity},
    }
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_name}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    equity.rename("equity").to_csv(out / f"{run_name}_equity.csv")
    pd.DataFrame(
        [
            {
                "symbol": t.symbol,
                "entry": t.entry_timestamp,
                "exit": t.exit_timestamp,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl": t.pnl,
                "reason": t.metadata.get("exit_reason"),
            }
            for t in trades
        ]
    ).to_csv(out / f"{run_name}_trades.csv", index=False)
    print(json.dumps({k: summary[k] for k in ("variant", "periods", "avg_gross_exposure", "trades")}, indent=2))


def main() -> None:
    args = parse_args()
    membership = IntervalMembership(Path(args.membership))
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    if args.benchmarks:
        benchmarks(args, membership)
    if args.variant:
        run_variant(args, membership)


if __name__ == "__main__":
    main()
