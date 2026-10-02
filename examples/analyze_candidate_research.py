"""Summarize barbell candidate research exports.

This utility is intentionally simple: it reads the passive candidate CSV emitted
by ``run_market_wizards_strategies.py`` and reports whether selected candidates
had better forward returns than rejected-but-eligible candidates by symbol and
episode. It is the first diagnostic layer before fitting any EV/meta-label gate.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _summary(frame: pd.DataFrame, value_col: str) -> pd.DataFrame:
    return frame.groupby("symbol", dropna=False)[value_col].agg(["count", "mean", "median"]).sort_values(
        "mean",
        ascending=False,
    )


def _episode_summary(frame: pd.DataFrame, value_col: str) -> pd.DataFrame:
    return frame.groupby("episode", dropna=False)[value_col].agg(["count", "mean", "median"])


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    out = frame.reset_index()
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


def build_report(csv_path: Path, horizon: int) -> str:
    frame = pd.read_csv(csv_path)
    value_col = f"side_fwd_return_{horizon}d"
    if value_col not in frame.columns:
        raise ValueError(f"{csv_path} does not contain {value_col}")

    selected = frame[frame["selected"] == True]  # noqa: E712 - CSV booleans can round-trip as bools.
    rejected = frame[frame["selected"] != True]  # noqa: E712

    lines = [
        "# Candidate Research Summary",
        "",
        f"CSV: `{csv_path}`",
        f"Horizon: `{horizon}` trading days",
        "",
        "## Coverage",
        "",
        f"- Rows: {len(frame):,}",
        f"- Symbols: {', '.join(sorted(str(s) for s in frame['symbol'].dropna().unique()))}",
        f"- Date range: {frame['date'].min()} to {frame['date'].max()}",
        f"- Selected ratio: {frame['selected'].mean():.4f}",
        f"- Active ratio: {frame['active_any'].mean():.4f}",
        f"- Forward-label coverage: {frame[value_col].notna().mean():.4f}",
        "",
        "## Selected Candidates By Symbol",
        "",
        _markdown_table(_summary(selected, value_col)),
        "",
        "## Rejected Candidates By Symbol",
        "",
        _markdown_table(_summary(rejected, value_col)),
        "",
        "## All Candidates By Symbol",
        "",
        _markdown_table(_summary(frame, value_col)),
        "",
        "## Selected Candidates By Episode",
        "",
        _markdown_table(_episode_summary(selected, value_col)),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize candidate research CSV exports.")
    parser.add_argument("--csv", required=True, type=Path, help="Candidate research CSV to analyze.")
    parser.add_argument("--horizon", type=int, default=21, help="Forward-return horizon in trading days.")
    parser.add_argument("--output", type=Path, help="Optional Markdown output path.")
    args = parser.parse_args()

    report = build_report(args.csv, args.horizon)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
