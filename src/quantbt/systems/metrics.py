from __future__ import annotations

import numpy as np
import pandas as pd

from quantbt.analytics.performance import compute_drawdown, compute_metrics


def summarize(equity: pd.Series, gross_leverage: pd.Series | None = None) -> dict[str, float]:
    """CAGR, Sharpe, max drawdown and friends from a daily equity curve, via the repo's metric code."""
    equity = equity.dropna()
    returns = equity.pct_change().fillna(0.0)
    metrics = compute_metrics(
        equity_curve=equity,
        returns=returns,
        drawdown=compute_drawdown(equity),
        closed_trades=[],
        periods_per_year=252,
    )
    out = {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "cagr": metrics["annualized_return"],
        "sharpe": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "calmar": metrics["calmar_ratio"],
        "annual_vol": float(returns.std(ddof=0) * np.sqrt(252)),
        "max_drawdown_duration_days": metrics["max_drawdown_duration_days"],
    }
    if gross_leverage is not None:
        lev = gross_leverage.reindex(equity.index).dropna()
        out["avg_gross_leverage"] = float(lev.mean()) if len(lev) else 0.0
        out["max_gross_leverage"] = float(lev.max()) if len(lev) else 0.0
    return out

