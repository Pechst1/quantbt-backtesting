from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from quantbt.engine import BacktestEngine


@dataclass(slots=True)
class WalkForwardFoldResult:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    best_params: dict[str, Any]
    train_metric: float
    test_metric: float


def walk_forward_optimize(
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    interval: str,
    train_years: int,
    test_years: int,
    step_years: int,
    param_grid: list[dict[str, Any]],
    build_data_handler: Callable[[list[str], datetime, datetime, str], Any],
    build_strategy: Callable[[list[str], dict[str, Any]], Any],
    build_portfolio: Callable[[], Any],
    build_execution: Callable[[], Any],
    metric: str = "sharpe_ratio",
) -> pd.DataFrame:
    """
    Walk-forward optimization utility.

    For each fold:
    1) Train on [train_start, train_end) over the parameter grid.
    2) Select parameters maximizing the chosen metric.
    3) Evaluate out-of-sample on [test_start, test_end).
    """

    if not param_grid:
        raise ValueError("param_grid must contain at least one parameter set.")
    if train_years <= 0 or test_years <= 0 or step_years <= 0:
        raise ValueError("train_years, test_years and step_years must be positive.")

    results: list[WalkForwardFoldResult] = []
    fold = 0
    train_start = pd.Timestamp(start)
    final_end = pd.Timestamp(end)

    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_start = train_end
        test_end = test_start + pd.DateOffset(years=test_years)
        if test_end > final_end:
            break

        best_params = param_grid[0]
        best_score = -np.inf
        for params in param_grid:
            data_handler = build_data_handler(symbols, train_start.to_pydatetime(), train_end.to_pydatetime(), interval)
            strategy = build_strategy(symbols, params)
            engine = BacktestEngine(
                data_handler=data_handler,
                strategy=strategy,
                portfolio=build_portfolio(),
                execution_handler=build_execution(),
            )
            train_result = engine.run(run_name=f"wf_train_{fold}")
            score = float(train_result.report.metrics.get(metric, float("-inf")))
            if score > best_score:
                best_score = score
                best_params = params

        test_data_handler = build_data_handler(
            symbols,
            test_start.to_pydatetime(),
            test_end.to_pydatetime(),
            interval,
        )
        test_strategy = build_strategy(symbols, best_params)
        test_engine = BacktestEngine(
            data_handler=test_data_handler,
            strategy=test_strategy,
            portfolio=build_portfolio(),
            execution_handler=build_execution(),
        )
        test_result = test_engine.run(run_name=f"wf_test_{fold}")
        test_score = float(test_result.report.metrics.get(metric, float("-inf")))

        results.append(
            WalkForwardFoldResult(
                fold=fold,
                train_start=train_start.to_pydatetime(),
                train_end=train_end.to_pydatetime(),
                test_start=test_start.to_pydatetime(),
                test_end=test_end.to_pydatetime(),
                best_params=dict(best_params),
                train_metric=best_score,
                test_metric=test_score,
            )
        )

        fold += 1
        train_start = train_start + pd.DateOffset(years=step_years)

    frame = pd.DataFrame(
        [
            {
                "fold": row.fold,
                "train_start": row.train_start,
                "train_end": row.train_end,
                "test_start": row.test_start,
                "test_end": row.test_end,
                "best_params": row.best_params,
                "train_metric": row.train_metric,
                "test_metric": row.test_metric,
            }
            for row in results
        ]
    )
    return frame

