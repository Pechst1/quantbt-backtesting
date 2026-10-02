from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from quantbt.core.models import ClosedTrade
from quantbt.portfolio.portfolio import PortfolioSnapshot


@dataclass(slots=True)
class PerformanceReport:
    metrics: dict[str, float]
    equity_curve: pd.Series
    returns: pd.Series
    drawdown: pd.Series
    monthly_returns: pd.DataFrame


def periods_per_year_from_interval(interval: str) -> int:
    mapping = {
        "1m": 252 * 390,
        "5m": 252 * 78,
        "15m": 252 * 26,
        "30m": 252 * 13,
        "60m": 252 * 7,
        "1h": 252 * 7,
        "1d": 252,
        "1wk": 52,
        "1mo": 12,
    }
    return mapping.get(interval, 252)


def compute_drawdown(equity_curve: pd.Series) -> pd.Series:
    rolling_max = equity_curve.cummax()
    return equity_curve / rolling_max - 1.0


def compute_drawdown_duration_days(drawdown: pd.Series) -> int:
    if drawdown.empty:
        return 0
    underwater = drawdown < 0
    max_duration_days = 0
    current_start = None
    for ts, flag in underwater.items():
        if flag and current_start is None:
            current_start = ts
        elif not flag and current_start is not None:
            duration = int((ts - current_start).days)
            max_duration_days = max(max_duration_days, duration)
            current_start = None
    if current_start is not None:
        duration = int((drawdown.index[-1] - current_start).days)
        max_duration_days = max(max_duration_days, duration)
    return max_duration_days


def _annualized_return(equity_curve: pd.Series, periods_per_year: int) -> float:
    if len(equity_curve) < 2:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1.0
    years = max((len(equity_curve) - 1) / periods_per_year, 1.0 / periods_per_year)
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def compute_metrics(
    equity_curve: pd.Series,
    returns: pd.Series,
    drawdown: pd.Series,
    closed_trades: list[ClosedTrade],
    periods_per_year: int,
) -> dict[str, float]:
    annualized_return = _annualized_return(equity_curve, periods_per_year)
    vol = returns.std(ddof=0)
    sharpe = float(np.sqrt(periods_per_year) * returns.mean() / vol) if vol > 0 else 0.0

    downside = returns[returns < 0]
    downside_std = downside.std(ddof=0)
    sortino = (
        float(np.sqrt(periods_per_year) * returns.mean() / downside_std)
        if downside_std > 0
        else 0.0
    )

    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0
    calmar = annualized_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
    max_drawdown_duration_days = compute_drawdown_duration_days(drawdown)

    trade_pnls = [trade.pnl for trade in closed_trades]
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]

    win_rate = (len(wins) / len(trade_pnls)) if trade_pnls else 0.0
    gross_profit = float(sum(wins))
    gross_loss = float(sum(losses))
    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )

    return {
        "annualized_return": float(annualized_return),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "max_drawdown": max_drawdown,
        "calmar_ratio": float(calmar),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "num_closed_trades": float(len(trade_pnls)),
        "max_drawdown_duration_days": float(max_drawdown_duration_days),
    }


def monthly_returns_heatmap(returns: pd.Series) -> pd.DataFrame:
    if returns.empty:
        return pd.DataFrame()
    monthly = returns.resample("ME").apply(lambda x: (1.0 + x).prod() - 1.0)
    monthly_df = monthly.to_frame(name="return")
    monthly_df["year"] = monthly_df.index.year
    monthly_df["month"] = monthly_df.index.month
    pivot = monthly_df.pivot(index="year", columns="month", values="return").sort_index()
    pivot = pivot.reindex(columns=list(range(1, 13)))
    pivot.columns = [calendar.month_abbr[m] for m in pivot.columns]
    return pivot


def build_performance_report(
    snapshots: list[PortfolioSnapshot],
    closed_trades: list[ClosedTrade],
    interval: str,
) -> PerformanceReport:
    if not snapshots:
        raise ValueError("Portfolio history is empty. Run a backtest before analytics.")

    index = [snapshot.timestamp for snapshot in snapshots]
    values = [snapshot.equity for snapshot in snapshots]
    equity_curve = pd.Series(values, index=pd.to_datetime(index), dtype=float).sort_index()
    returns = equity_curve.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    drawdown = compute_drawdown(equity_curve)

    periods_per_year = periods_per_year_from_interval(interval)
    metrics = compute_metrics(
        equity_curve=equity_curve,
        returns=returns,
        drawdown=drawdown,
        closed_trades=closed_trades,
        periods_per_year=periods_per_year,
    )

    return PerformanceReport(
        metrics=metrics,
        equity_curve=equity_curve,
        returns=returns,
        drawdown=drawdown,
        monthly_returns=monthly_returns_heatmap(returns),
    )
