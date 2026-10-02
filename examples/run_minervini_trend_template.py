"""
Backtest Minervini's Trend Template on point-in-time S&P 500 members.

Run examples/fetch_sp500_panel.py first. All rule parameters are the published ones
(see MinerviniTrendTemplateStrategy); nothing here is fitted to the results.
"""

from __future__ import annotations

import argparse
import bisect
import json
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
from quantbt.strategies.minervini import MinerviniTrendTemplateStrategy

SUBPERIODS = (("1999-2009", "1999-01-01", "2009-12-31"), ("2010-2019", "2010-01-01", "2019-12-31"), ("2020-2026", "2020-01-01", "2026-12-31"))


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--start", default="1998-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--market-filter", action="store_true", help="Only enter while SPY passes criteria 1-5.")
    parser.add_argument("--output-dir", default="reports/minervini")
    parser.add_argument("--run-name", default=None)
    return parser.parse_args()


def period_stats(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    rets = equity.pct_change().dropna()
    dd = equity / equity.cummax() - 1.0
    return {
        "cagr": float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan"),
        "ann_vol": float(rets.std() * np.sqrt(252)),
        "sharpe_rf0": float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else float("nan"),
        "max_drawdown": float(dd.min()),
    }


def main() -> None:
    args = parse_args()
    membership = IntervalMembership(Path(args.membership))
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    symbols = [s for s in membership.symbols if s in set(source.symbols)] + ["SPY"]
    missing = sorted(set(membership.symbols) - set(source.symbols))
    print(f"{len(symbols) - 1} of {len(membership.symbols)} historical members have prices; {len(missing)} do not.")

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
    strategy = MinerviniTrendTemplateStrategy(
        symbols=data.active_symbols,
        is_member=membership.is_member,
        market_symbol="SPY" if args.market_filter else None,
        last_bar_dates=source.listing_end_dates(),
    )
    portfolio = Portfolio(initial_cash=args.initial_cash, leverage=1.0)
    engine = BacktestEngine(
        data_handler=data,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=SimulatedExecutionHandler(ExecutionConfig()),
    )
    run_name = args.run_name or ("minervini_tt_spy_filter" if args.market_filter else "minervini_tt")
    result = engine.run(run_name=run_name)

    equity = result.report.equity_curve
    first_trade = min((t.entry_timestamp for t in portfolio.closed_trades if t.entry_timestamp), default=None)
    if first_trade is not None:
        equity = equity[equity.index >= pd.Timestamp(first_trade)]
    spy = data._frames["SPY"]["close"].reindex(equity.index)

    exposure = pd.Series([s.gross_exposure / s.equity for s in portfolio.history], index=[s.timestamp for s in portfolio.history])
    trades = portfolio.closed_trades
    pnls = np.array([t.pnl for t in trades])
    rets = np.array([t.exit_price / t.entry_price - 1.0 for t in trades if t.entry_price > 0])
    holds = np.array([(t.exit_timestamp - t.entry_timestamp).days for t in trades if t.entry_timestamp and t.exit_timestamp])

    summary = {
        "run_name": run_name,
        "rules": {
            "max_positions": strategy.max_positions,
            "min_rs_rank": strategy.min_rs_rank,
            "stop_loss_pct": strategy.stop_loss_pct,
            "market_filter": strategy.market_symbol,
        },
        "symbols_with_prices": len(symbols) - 1,
        "historical_members": len(membership.symbols),
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "strategy": period_stats(equity),
        "spy_total_return": period_stats(spy),
        "subperiods": {
            name: {
                "strategy": period_stats(equity[(equity.index >= lo) & (equity.index <= hi)]),
                "spy": period_stats(spy[(spy.index >= lo) & (spy.index <= hi)]),
            }
            for name, lo, hi in SUBPERIODS
        },
        "avg_gross_exposure": float(exposure[exposure.index >= equity.index[0]].mean()),
        "trades": {
            "count": len(trades),
            "win_rate": float((pnls > 0).mean()) if len(pnls) else 0.0,
            "avg_return": float(rets.mean()) if len(rets) else 0.0,
            "avg_win": float(rets[rets > 0].mean()) if (rets > 0).any() else 0.0,
            "avg_loss": float(rets[rets <= 0].mean()) if (rets <= 0).any() else 0.0,
            "median_hold_days": float(np.median(holds)) if len(holds) else 0.0,
            "exit_reasons": dict(Counter(str(t.metadata.get("exit_reason", t.metadata.get("reason", "?"))) for t in trades)),
        },
        "engine": {"fills": result.fills, "risk_rejections": result.risk_rejections, "margin_calls": result.margin_calls},
        "current_screen": dict(sorted(strategy.last_screen.items(), key=lambda kv: -kv[1])),
        "open_positions": {s: int(p.quantity) for s, p in portfolio.positions.items() if p.quantity},
    }

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_name}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    pd.DataFrame({"strategy": equity, "spy": spy / spy.iloc[0] * equity.iloc[0]}).to_csv(out / f"{run_name}_equity.csv")
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
            }
            for t in trades
        ]
    ).to_csv(out / f"{run_name}_trades.csv", index=False)
    print(json.dumps({k: summary[k] for k in ("start", "end", "strategy", "spy_total_return", "subperiods", "avg_gross_exposure", "trades")}, indent=2))


if __name__ == "__main__":
    main()
