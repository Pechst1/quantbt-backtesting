"""Walk-forward evaluation for a candidate-level EV gate.

The barbell strategy's candidate research CSV contains passive forward labels.
This script uses those labels only out-of-sample: each test year is scored from
historical candidates whose holding horizon has already matured before that
year starts. The output is a research diagnostic, not a live trading model.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GateConfig:
    horizon: int
    top_n: int
    hurdle: float
    tail_limit: float
    min_obs: int
    shrink: float
    start_year: int


KEY_LEVELS = (
    ("symbol", "side_bucket", "theme", "score_bucket", "coherence_bucket"),
    ("symbol", "side_bucket", "score_bucket"),
    ("asset_class", "side_bucket", "score_bucket"),
    ("symbol", "side_bucket"),
    ("asset_class", "side_bucket"),
)


def _profit_factor(values: pd.Series) -> float:
    pos = float(values[values > 0.0].sum())
    neg = float(values[values < 0.0].sum())
    if neg == 0.0:
        return 0.0 if pos == 0.0 else float("inf")
    return pos / abs(neg)


def _summary(frame: pd.DataFrame, label_col: str) -> dict[str, float]:
    values = frame[label_col].dropna()
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


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    out = frame.reset_index(drop=False)
    headers = [str(col) for col in out.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, row in out.iterrows():
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.6f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _feature_engineer(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["side_bucket"] = np.where(out["side"] > 0.0, "LONG", np.where(out["side"] < 0.0, "SHORT", "FLAT"))
    out["score_bucket"] = pd.cut(
        out["abs_score"].fillna(0.0),
        bins=[-np.inf, 0.8, 1.2, 1.8, 2.5, np.inf],
        labels=["lt_0p8", "0p8_1p2", "1p2_1p8", "1p8_2p5", "gt_2p5"],
    ).astype(str)
    out["coherence_bucket"] = pd.cut(
        out["coherence"].fillna(0.0),
        bins=[-np.inf, 0.25, 0.50, 0.75, 1.00, np.inf],
        labels=["lt_0p25", "0p25_0p5", "0p5_0p75", "0p75_1p0", "gt_1p0"],
    ).astype(str)
    out["trend_bucket"] = pd.cut(
        out["trend_distance_atr"].fillna(0.0),
        bins=[-np.inf, -1.0, 0.0, 1.0, np.inf],
        labels=["below_1atr", "below_0", "above_0", "above_1atr"],
    ).astype(str)
    out["vol_bucket"] = pd.cut(
        out["realized_vol_21"].fillna(0.0),
        bins=[-np.inf, 0.15, 0.30, 0.60, np.inf],
        labels=["low", "normal", "high", "crisis"],
    ).astype(str)
    return out


def _build_stats(train: pd.DataFrame, label_col: str, tail_col: str, config: GateConfig) -> list[dict[tuple, tuple[int, float, float]]]:
    stats: list[dict[tuple, tuple[int, float, float]]] = []
    for keys in KEY_LEVELS:
        table: dict[tuple, tuple[int, float, float]] = {}
        grouped = train.groupby(list(keys), dropna=False)
        for key, group in grouped:
            values = group[label_col].dropna()
            if values.empty:
                continue
            tail_values = group[tail_col].dropna()
            key_tuple = key if isinstance(key, tuple) else (key,)
            table[key_tuple] = (
                int(len(values)),
                float(values.mean()),
                float(tail_values.quantile(0.10)) if not tail_values.empty else -1.0,
            )
        stats.append(table)
    return stats


def _predict_row(
    row: pd.Series,
    stats: list[dict[tuple, tuple[int, float, float]]],
    global_mean: float,
    global_tail: float,
    config: GateConfig,
) -> tuple[float, float, int, str]:
    for keys, table in zip(KEY_LEVELS, stats, strict=True):
        key = tuple(row[key] for key in keys)
        found = table.get(key)
        if found is None:
            continue
        count, mean, tail = found
        if count < config.min_obs:
            continue
        weight = count / (count + config.shrink)
        pred = weight * mean + (1.0 - weight) * global_mean
        return float(pred), float(tail), int(count), "+".join(keys)
    return float(global_mean), float(global_tail), int(len(stats)), "global"


def walk_forward_predictions(frame: pd.DataFrame, config: GateConfig) -> pd.DataFrame:
    label_col = f"side_fwd_return_{config.horizon}d"
    tail_col = f"side_mae_{config.horizon}d"
    required = {"date", "symbol", "side", "abs_score", label_col, tail_col}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"candidate CSV missing required columns: {sorted(missing)}")

    work = _feature_engineer(frame)
    work = work[(work["side"] != 0.0) & (work["abs_score"] > 0.0) & work[label_col].notna()].copy()
    work.sort_values(["date", "symbol"], inplace=True)

    years = [year for year in sorted(work["date"].dt.year.unique()) if year >= config.start_year]
    predicted_frames: list[pd.DataFrame] = []
    for year in years:
        test_start = pd.Timestamp(year=year, month=1, day=1)
        test_end = pd.Timestamp(year=year, month=12, day=31)
        train_cutoff = test_start - pd.Timedelta(days=max(config.horizon * 2, 31))
        train = work[work["date"] <= train_cutoff]
        test = work[(work["date"] >= test_start) & (work["date"] <= test_end)].copy()
        if train.empty or test.empty:
            continue
        global_mean = float(train[label_col].mean())
        global_tail = float(train[tail_col].quantile(0.10))
        stats = _build_stats(train, label_col, tail_col, config)
        preds = [
            _predict_row(row, stats, global_mean, global_tail, config)
            for _, row in test.iterrows()
        ]
        pred_frame = pd.DataFrame(
            preds,
            columns=["pred_ev", "pred_tail", "pred_obs", "pred_level"],
            index=test.index,
        )
        test = pd.concat([test, pred_frame], axis=1)
        test["test_year"] = year
        predicted_frames.append(test)

    if not predicted_frames:
        return pd.DataFrame()
    out = pd.concat(predicted_frames, axis=0).sort_values(["date", "symbol"])
    out["ev_gate_pass"] = (out["pred_ev"] > config.hurdle) & (out["pred_tail"] > config.tail_limit)
    return out


def _select_ev_top_n(predictions: pd.DataFrame, config: GateConfig) -> pd.DataFrame:
    eligible = predictions[predictions["ev_gate_pass"]].copy()
    if eligible.empty:
        return eligible
    eligible.sort_values(["date", "pred_ev", "abs_score"], ascending=[True, False, False], inplace=True)
    return eligible.groupby("date", group_keys=False).head(config.top_n)


def build_report(predictions: pd.DataFrame, config: GateConfig, csv_path: Path) -> str:
    label_col = f"side_fwd_return_{config.horizon}d"
    if predictions.empty:
        return "No predictions generated.\n"

    selected = predictions[predictions["selected"] == True]  # noqa: E712
    selected_gate = selected[selected["ev_gate_pass"]]
    ev_top_n = _select_ev_top_n(predictions, config)

    summary = pd.DataFrame(
        {
            "actual_selected": _summary(selected, label_col),
            "actual_selected_ev_gate": _summary(selected_gate, label_col),
            "ev_rank_top_n": _summary(ev_top_n, label_col),
        }
    ).T

    by_symbol = ev_top_n.groupby("symbol")[label_col].agg(["count", "mean", "median"]).sort_values(
        "mean",
        ascending=False,
    )
    by_episode = ev_top_n.groupby("episode")[label_col].agg(["count", "mean", "median"])
    pass_rate_by_symbol = predictions.groupby("symbol")["ev_gate_pass"].mean().to_frame("pass_rate").sort_values(
        "pass_rate",
        ascending=False,
    )

    lines = [
        "# Walk-Forward Candidate EV Gate",
        "",
        f"CSV: `{csv_path}`",
        f"Horizon: `{config.horizon}` trading days",
        f"Top-N: `{config.top_n}`",
        f"EV hurdle: `{config.hurdle:.4f}`",
        f"Tail limit: `{config.tail_limit:.4f}`",
        f"Minimum bucket observations: `{config.min_obs}`",
        "",
        "## Summary",
        "",
        _markdown_table(summary),
        "",
        "## EV Top-N By Symbol",
        "",
        _markdown_table(by_symbol),
        "",
        "## EV Top-N By Episode",
        "",
        _markdown_table(by_episode),
        "",
        "## Gate Pass Rate By Symbol",
        "",
        _markdown_table(pass_rate_by_symbol),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an expanding walk-forward candidate EV gate.")
    parser.add_argument("--csv", required=True, type=Path, help="Candidate research CSV.")
    parser.add_argument("--horizon", type=int, default=21, help="Forward label horizon in trading days.")
    parser.add_argument("--top-n", type=int, default=3, help="Daily candidate count for EV-ranked selection.")
    parser.add_argument("--hurdle", type=float, default=0.0025, help="Minimum predicted side-adjusted return.")
    parser.add_argument("--tail-limit", type=float, default=-0.10, help="Minimum predicted 10th percentile side MAE.")
    parser.add_argument("--min-obs", type=int, default=40, help="Minimum observations for a bucket.")
    parser.add_argument("--shrink", type=float, default=80.0, help="Shrinkage strength toward the expanding global mean.")
    parser.add_argument("--start-year", type=int, default=2010, help="First walk-forward test year.")
    parser.add_argument("--predictions-output", type=Path, help="Optional CSV path for scored candidates.")
    parser.add_argument("--report-output", type=Path, help="Optional Markdown report output path.")
    args = parser.parse_args()

    config = GateConfig(
        horizon=args.horizon,
        top_n=args.top_n,
        hurdle=args.hurdle,
        tail_limit=args.tail_limit,
        min_obs=args.min_obs,
        shrink=args.shrink,
        start_year=args.start_year,
    )
    frame = pd.read_csv(args.csv)
    predictions = walk_forward_predictions(frame, config)
    if args.predictions_output:
        args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(args.predictions_output, index=False)
    report = build_report(predictions, config, args.csv)
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
