"""
Backtest the pre-registered Stock Market Wizards variants (Okumus, Cook, Shaw/Cohen) on
point-in-time S&P 500 members.

Run examples/fetch_sp500_panel.py first. Rules and the variant list are fixed in
reports/stock_market_wizards/PREREGISTRATION.md; nothing here is fitted to the results.

    python examples/run_stock_market_wizards.py --variant H1-A
    python examples/run_stock_market_wizards.py --benchmarks
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
from quantbt.strategies.wizards import CookBreadthTimingStrategy, OkumusDeepValueStrategy, ShortTermReversalStrategy

VARIANTS: dict[str, dict] = {
    "H1-A": dict(kind="okumus", min_drop=0.60, max_positions=10),
    "H1-B": dict(kind="okumus", min_drop=0.50, max_positions=10),
    "H1-C": dict(kind="okumus", min_drop=0.60, max_positions=20),
    "H2-A": dict(kind="cook", mode="cash"),
    "H2-B": dict(kind="cook", mode="levered"),
    "H2-C": dict(kind="cook", mode="sell_tops"),
    # Follow-up overlays, pre-registered after the first nine runs.
    "H2-D": dict(kind="cook", mode="levered", signal_exposure=1.25),
    "H2-E": dict(kind="cook", mode="levered", signal_exposure=1.5),
    "H2-F": dict(kind="cook", mode="levered", signal_exposure=1.5, trend_sma=200),
    "H2-G": dict(kind="cook", mode="levered", signal_exposure=2.0, trend_sma=200),
    "H3-A": dict(kind="reversal", lookback=5, freq="W"),
    "H3-B": dict(kind="reversal_ls_vector", lookback=5, freq="W"),
    "H3-C": dict(kind="reversal", lookback=21, freq="M"),
}
PERIODS = (
    ("in_sample_1999_2019", "1999-01-01", "2019-12-31"),
    ("holdout_2020_2026", "2020-01-01", "2026-12-31"),
    ("full_1999_2026", "1999-01-01", "2026-12-31"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(VARIANTS))
    parser.add_argument("--benchmarks", action="store_true", help="Write SPY and equal-weight member benchmarks.")
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--membership", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--start", default="1998-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--output-dir", default="reports/stock_market_wizards")
    return parser.parse_args()


def by_period(equity: pd.Series) -> dict[str, dict[str, float]]:
    return {name: period_stats(equity[(equity.index >= lo) & (equity.index <= hi)]) for name, lo, hi in PERIODS}


def load_closes(args: argparse.Namespace, membership: IntervalMembership, start: str) -> pd.DataFrame:
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    closes = pd.DataFrame({s: f["adj_close"] for s, f in source._frames.items()}).sort_index()
    return closes.loc[start : args.end]


def period_end_dates(index: pd.DatetimeIndex, freq: str) -> set:
    """Last trading day of each calendar week ("W") or month ("M") in the trading calendar."""
    key = index.to_period("W-FRI" if freq == "W" else "M")
    last = index.to_series().groupby(key).max()
    return {ts.date() for ts in last}


def member_mask(closes: pd.DataFrame, membership: IntervalMembership, dates) -> pd.DataFrame:
    rows = {ts: [membership.is_member(s, ts.date()) for s in closes.columns] for ts in dates}
    return pd.DataFrame.from_dict(rows, orient="index", columns=closes.columns)


def benchmarks(args: argparse.Namespace, membership: IntervalMembership) -> None:
    closes = load_closes(args, membership, args.start)
    spy = closes.pop("SPY")
    rets = closes.pct_change(fill_method=None)
    month_starts = closes.index.to_series().groupby(closes.index.to_period("M")).first()
    mask = member_mask(closes, membership, month_starts)
    weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    for ts in month_starts:
        w = (closes.loc[ts].notna().values & mask.loc[ts].values).astype(float)
        weights.loc[ts] = w / w.sum() if w.sum() else w
    weights = weights.ffill().shift(1)
    ew = (1.0 + (weights * rets.fillna(0.0)).sum(axis=1)).cumprod()
    out = {"SPY": by_period(spy.dropna()), "EW_members": by_period(ew[ew.index >= "1999-01-01"])}
    Path(args.output_dir, "benchmarks_summary.json").write_text(json.dumps(out, indent=2))
    pd.DataFrame({"spy": spy, "ew_members": ew}).to_csv(Path(args.output_dir, "benchmarks_equity.csv"))
    print(json.dumps(out, indent=2))


def reversal_long_short_vector(args: argparse.Namespace, membership: IntervalMembership, rules: dict) -> None:
    """
    H3-B: dollar-neutral weekly reversal (long bottom decile, short top decile), outside the
    engine. Signal on the week's last close, trade at the next open, 10 bps per side on the
    traded notional, no borrow fee, cash earns nothing.
    """
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    frames = {s: f for s, f in source._frames.items() if s != "SPY"}
    adj_close = pd.DataFrame({s: f["adj_close"] for s, f in frames.items()}).sort_index().loc[args.start : args.end]
    # Adjusted open = open x (adj_close / close), the same adjustment the engine applies.
    adj_open = pd.DataFrame({s: f["open"] * f["adj_close"] / f["close"] for s, f in frames.items()}).sort_index()
    adj_open = adj_open.reindex(adj_close.index)
    lookback = rules["lookback"]
    signal_dates = sorted(period_end_dates(adj_close.index, rules["freq"]))
    signal_ts = [pd.Timestamp(d) for d in signal_dates]
    mask = member_mask(adj_close, membership, signal_ts)
    past = adj_close.shift(lookback)
    cost = 0.0010

    dates = adj_close.index
    pos = {d: i for i, d in enumerate(dates)}
    weights = pd.Series(0.0, index=adj_close.columns)
    equity = [1.0]
    eq_dates = [dates[0]]
    turnover_total = 0.0
    sig_set = set(signal_ts)
    pending: pd.Series | None = None
    for k in range(1, len(dates)):
        d, prev = dates[k], dates[k - 1]
        # Close-to-open on the old book, open-to-close on the new book if a trade fills today.
        o, c, pc = adj_open.loc[d], adj_close.loc[d], adj_close.loc[prev]
        if pending is not None:
            r1 = (o / pc - 1.0).where(weights != 0, 0.0).fillna(0.0)
            eq = equity[-1] * (1.0 + float((weights * r1).sum()))
            traded = float((pending - weights).abs().sum())
            turnover_total += traded
            eq *= 1.0 - cost * traded
            weights = pending
            pending = None
            r2 = (c / o - 1.0).where(weights != 0, 0.0).fillna(0.0)
            eq *= 1.0 + float((weights * r2).sum())
        else:
            r = (c / pc - 1.0).where(weights != 0, 0.0).fillna(0.0)
            eq = equity[-1] * (1.0 + float((weights * r).sum()))
        equity.append(eq)
        eq_dates.append(d)
        if d in sig_set and k >= lookback:
            ret = c / past.loc[d] - 1.0
            ok = ret.notna() & mask.loc[d] & adj_open.iloc[min(k + 1, len(dates) - 1)].notna()
            ranked = ret[ok].sort_values()
            n = int(round(len(ranked) * 0.10))
            if n > 0:
                w = pd.Series(0.0, index=adj_close.columns)
                w[ranked.index[:n]] = 0.5 / n
                w[ranked.index[-n:]] = -0.5 / n
                pending = w
    eq = pd.Series(equity, index=eq_dates)
    eq = eq[eq.index >= "1999-01-01"]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    summary = {
        "variant": "H3-B",
        "rules": rules,
        "periods": by_period(eq),
        "calendar_years": {str(y): float(g.iloc[-1] / g.iloc[0] - 1.0) for y, g in eq.groupby(eq.index.year)},
        "annual_turnover_gross": turnover_total / ((dates[-1] - dates[0]).days / 365.25),
        "years": years,
    }
    write_summary(args, "H3-B", summary, eq, trades=None)


def write_summary(args, run_name, summary, equity, trades) -> None:
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_name}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    equity.rename("equity").to_csv(out / f"{run_name}_equity.csv")
    if trades is not None:
        trades.to_csv(out / f"{run_name}_trades.csv", index=False)
    print(json.dumps({k: summary[k] for k in summary if k in ("variant", "periods", "avg_gross_exposure", "trades", "strategy_stats")}, indent=2, default=str))


def run_variant(args: argparse.Namespace, membership: IntervalMembership) -> None:
    rules = dict(VARIANTS[args.variant])
    kind = rules.pop("kind")
    if kind == "reversal_ls_vector":
        reversal_long_short_vector(args, membership, rules)
        return
    # Cook needs ~2 years of breadth history before its first signal, so it starts its
    # warm-up at the beginning of the panel instead of 1998.
    start = "1996-01-01" if kind == "cook" else args.start
    source = ParquetPanelSource(args.prices, symbols=membership.symbols + ["SPY"])
    symbols = [s for s in membership.symbols if s in set(source.symbols)] + ["SPY"]
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(
            symbols=symbols,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(args.end),
            cache_dir=Path(".cache/market_data"),
            required_symbols={"SPY"},
        ),
        source=source,
    )
    leverage = 1.0
    if kind == "okumus":
        strategy = OkumusDeepValueStrategy(symbols=data.active_symbols, is_member=membership.is_member, **rules)
    elif kind == "cook":
        strategy = CookBreadthTimingStrategy(symbols=data.active_symbols, is_member=membership.is_member, **rules)
        leverage = 2.0 if rules["mode"] == "levered" else 1.0  # margin limit, not the exposure
    else:
        spy_index = source._frames["SPY"].loc[start : args.end].index
        signal_dates = period_end_dates(spy_index, rules.pop("freq"))
        strategy = ShortTermReversalStrategy(
            symbols=data.active_symbols, is_member=membership.is_member, signal_dates=signal_dates, **rules
        )
    portfolio = Portfolio(initial_cash=args.initial_cash, leverage=leverage)
    engine = BacktestEngine(
        data_handler=data,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=SimulatedExecutionHandler(ExecutionConfig()),
    )
    result = engine.run(run_name=args.variant)

    equity = result.report.equity_curve
    equity = equity[equity.index >= "1999-01-01"]
    exposure = pd.Series([s.gross_exposure / s.equity for s in portfolio.history], index=[s.timestamp for s in portfolio.history])
    trades = portfolio.closed_trades
    rets = np.array([t.exit_price / t.entry_price - 1.0 for t in trades if t.entry_price > 0])
    holds = np.array([(t.exit_timestamp - t.entry_timestamp).days for t in trades if t.entry_timestamp and t.exit_timestamp])
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    stats = {k: v for k, v in vars(strategy).items() if isinstance(v, (int, float, str)) and not k.startswith("_")}
    summary = {
        "variant": args.variant,
        "rules": VARIANTS[args.variant],
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "periods": by_period(equity),
        "calendar_years": {str(y): float(g.iloc[-1] / g.iloc[0] - 1.0) for y, g in equity.groupby(equity.index.year)},
        "avg_gross_exposure": float(exposure[exposure.index >= equity.index[0]].mean()),
        "strategy_stats": stats,
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
        "engine": {
            "fills": result.fills,
            "rejected_orders": result.rejected_orders,
            "risk_rejections": result.risk_rejections,
            "margin_interest_paid": result.margin_interest_paid,
        },
        "open_positions": {s: int(p.quantity) for s, p in portfolio.positions.items() if p.quantity},
    }
    trade_frame = pd.DataFrame(
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
    )
    write_summary(args, args.variant, summary, equity, trade_frame)
    if kind == "cook":
        log = pd.DataFrame(strategy.breadth_log, columns=["date", "breadth", "cum_breadth", "state"])
        log.to_csv(Path(args.output_dir, f"{args.variant}_breadth.csv"), index=False)


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
