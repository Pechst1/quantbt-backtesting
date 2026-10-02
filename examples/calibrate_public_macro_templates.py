"""Walk-forward calibration harness for public macro factor templates.

This script tests whether the public macro z-score fields can predict forward
returns before they are hard-coded into strategy templates. It is intentionally
diagnostic: it does not change live strategy behavior.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


FACTOR_COLUMNS = ("z_growth", "z_inflation", "z_policy", "z_liquidity", "z_credit")
EXTRA_COLUMNS = ("coherence", "velocity_signal", "trend_distance_atr", "realized_vol_21")


@dataclass(frozen=True)
class CalibrationConfig:
    horizon: int
    start_year: int
    min_obs: int
    ridge: float
    top_n: int
    hurdle: float


def _profit_factor(values: pd.Series) -> float:
    pos = float(values[values > 0.0].sum())
    neg = float(values[values < 0.0].sum())
    if neg == 0.0:
        return 0.0 if pos == 0.0 else float("inf")
    return pos / abs(neg)


def _summary(values: pd.Series) -> dict[str, float]:
    values = values.dropna()
    if values.empty:
        return {
            "count": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "hit_rate": 0.0,
            "profit_factor": 0.0,
            "sum_return": 0.0,
        }
    return {
        "count": float(len(values)),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "hit_rate": float((values > 0.0).mean()),
        "profit_factor": float(_profit_factor(values)),
        "sum_return": float(values.sum()),
    }


def _fit_ridge(train: pd.DataFrame, feature_cols: list[str], label_col: str, ridge: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = train[feature_cols].to_numpy(dtype=float)
    y = train[label_col].to_numpy(dtype=float)
    mean = np.nanmean(x, axis=0)
    std = np.nanstd(x, axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    xz = np.nan_to_num((x - mean) / std, nan=0.0)
    design = np.column_stack([np.ones(len(xz)), xz])
    penalty = np.eye(design.shape[1]) * float(ridge)
    penalty[0, 0] = 0.0
    beta = np.linalg.pinv(design.T @ design + penalty) @ design.T @ y
    return beta, mean, std


def _predict(frame: pd.DataFrame, feature_cols: list[str], beta: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    x = frame[feature_cols].to_numpy(dtype=float)
    xz = np.nan_to_num((x - mean) / std, nan=0.0)
    design = np.column_stack([np.ones(len(xz)), xz])
    return design @ beta


def walk_forward_calibration(frame: pd.DataFrame, config: CalibrationConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    label_col = f"fwd_return_{config.horizon}d"
    side_label_col = f"side_fwd_return_{config.horizon}d"
    required = {"date", "symbol", "selected", "side", label_col, side_label_col, *FACTOR_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"candidate CSV missing required columns: {sorted(missing)}")

    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    feature_cols = [col for col in [*FACTOR_COLUMNS, *EXTRA_COLUMNS] if col in work.columns]
    for col in feature_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    work[label_col] = pd.to_numeric(work[label_col], errors="coerce")
    work[side_label_col] = pd.to_numeric(work[side_label_col], errors="coerce")
    work = work[work[label_col].notna()].sort_values(["date", "symbol"]).copy()

    predictions: list[pd.DataFrame] = []
    coefficient_rows: list[dict[str, object]] = []
    for year in [year for year in sorted(work["date"].dt.year.unique()) if year >= config.start_year]:
        test_start = pd.Timestamp(year=year, month=1, day=1)
        test_end = pd.Timestamp(year=year, month=12, day=31)
        train_cutoff = test_start - pd.Timedelta(days=max(config.horizon * 2, 31))
        train_pool = work[work["date"] <= train_cutoff]
        test_pool = work[(work["date"] >= test_start) & (work["date"] <= test_end)].copy()
        if train_pool.empty or test_pool.empty:
            continue

        pred_parts: list[pd.DataFrame] = []
        for symbol, test_symbol in test_pool.groupby("symbol", sort=False):
            train_symbol = train_pool[train_pool["symbol"] == symbol].copy()
            if len(train_symbol) < config.min_obs:
                continue
            beta, mean, std = _fit_ridge(train_symbol, feature_cols, label_col, config.ridge)
            scored = test_symbol.copy()
            scored["pred_return"] = _predict(scored, feature_cols, beta, mean, std)
            scored["pred_side"] = np.where(scored["pred_return"] > 0.0, 1.0, -1.0)
            scored["pred_abs"] = np.abs(scored["pred_return"])
            scored["model_side_return"] = scored["pred_side"] * scored[label_col]
            scored["test_year"] = year
            pred_parts.append(scored)
            coefficient_rows.append(
                {
                    "test_year": year,
                    "symbol": symbol,
                    "obs": int(len(train_symbol)),
                    **{f"beta_{name}": float(value) for name, value in zip(["intercept", *feature_cols], beta, strict=True)},
                }
            )
        if pred_parts:
            predictions.append(pd.concat(pred_parts, ignore_index=True))

    if not predictions:
        return pd.DataFrame(), pd.DataFrame(coefficient_rows)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(coefficient_rows)


def _model_top_n(predictions: pd.DataFrame, config: CalibrationConfig) -> pd.DataFrame:
    eligible = predictions[predictions["pred_abs"] >= config.hurdle].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(["date", "pred_abs"], ascending=[True, False])
    return eligible.groupby("date", group_keys=False).head(config.top_n)


def build_report(predictions: pd.DataFrame, coefficients: pd.DataFrame, config: CalibrationConfig, csv_path: Path) -> str:
    if predictions.empty:
        return "No walk-forward predictions generated.\n"
    side_label_col = f"side_fwd_return_{config.horizon}d"
    selected = predictions[(predictions["selected"] == True) & (predictions["side"] != 0.0)]  # noqa: E712
    model_top = _model_top_n(predictions, config)
    by_symbol = model_top.groupby("symbol")["model_side_return"].agg(["count", "mean", "median"]).sort_values(
        "mean",
        ascending=False,
    )
    by_year = model_top.groupby("test_year")["model_side_return"].agg(["count", "mean", "median"])
    summary = {
        "actual_selected": _summary(selected[side_label_col]),
        "model_top_n": _summary(model_top["model_side_return"]),
    }
    coefficient_summary = pd.DataFrame()
    if not coefficients.empty:
        beta_cols = [col for col in coefficients.columns if col.startswith("beta_")]
        coefficient_summary = coefficients.groupby("symbol")[beta_cols].mean().sort_index()

    def md_table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_No rows._"
        out = frame.reset_index()
        lines = [
            "| " + " | ".join(map(str, out.columns)) + " |",
            "| " + " | ".join("---" for _ in out.columns) + " |",
        ]
        for _, row in out.iterrows():
            cells = [f"{value:.6f}" if isinstance(value, float) else str(value) for value in row]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    lines = [
        "# Public Macro Template Calibration",
        "",
        f"CSV: `{csv_path}`",
        f"Horizon: `{config.horizon}` trading days",
        f"Start year: `{config.start_year}`",
        f"Top-N: `{config.top_n}`",
        f"Prediction hurdle: `{config.hurdle:.4f}`",
        "",
        "## Summary",
        "",
        md_table(pd.DataFrame(summary).T),
        "",
        "## Model Top-N By Symbol",
        "",
        md_table(by_symbol),
        "",
        "## Model Top-N By Test Year",
        "",
        md_table(by_year),
        "",
        "## Mean Coefficients By Symbol",
        "",
        md_table(coefficient_summary),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate public macro factor templates walk-forward.")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--horizon", type=int, default=21)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--min-obs", type=int, default=252)
    parser.add_argument("--ridge", type=float, default=25.0)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--hurdle", type=float, default=0.0025)
    parser.add_argument("--predictions-output", type=Path)
    parser.add_argument("--coefficients-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args()

    config = CalibrationConfig(
        horizon=args.horizon,
        start_year=args.start_year,
        min_obs=args.min_obs,
        ridge=args.ridge,
        top_n=args.top_n,
        hurdle=args.hurdle,
    )
    frame = pd.read_csv(args.csv)
    predictions, coefficients = walk_forward_calibration(frame, config)
    if args.predictions_output:
        args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(args.predictions_output, index=False)
    if args.coefficients_output:
        args.coefficients_output.parent.mkdir(parents=True, exist_ok=True)
        coefficients.to_csv(args.coefficients_output, index=False)
    report = build_report(predictions, coefficients, config, args.csv)
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
