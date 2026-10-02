from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


VARIANT_FILES = {
    "step_d_concentration": (
        "global_macro_barbell_concentration_2005_2025_final.json",
        "global_macro_barbell_concentration_diagnostics.json",
        "global_macro_barbell_concentration_attribution.json",
    ),
    "phase3_profit_rate": (
        "global_macro_barbell_phase3rate_2005_2025_final.json",
        "global_macro_barbell_phase3rate_diagnostics.json",
        "global_macro_barbell_phase3rate_attribution.json",
    ),
    "overlay_routing": (
        "global_macro_barbell_overlayroute_2005_2025_final.json",
        "global_macro_barbell_overlayroute_diagnostics.json",
        "global_macro_barbell_overlayroute_attribution.json",
    ),
    "focused_reallocation": (
        "global_macro_barbell_focusedrealloc_2005_2025_final.json",
        "global_macro_barbell_focusedrealloc_diagnostics.json",
        "global_macro_barbell_focusedrealloc_attribution.json",
    ),
}

EPISODES = [
    ("pre_crisis_2005_2007", "2005-01-01", "2007-01-01"),
    ("gfc_2007_2009", "2007-01-01", "2010-01-01"),
    ("post_gfc_qe_2010_2013", "2010-01-01", "2014-01-01"),
    ("commodity_growth_2014_2016", "2014-01-01", "2017-01-01"),
    ("late_cycle_2017_2019", "2017-01-01", "2020-01-01"),
    ("covid_2020", "2020-01-01", "2021-01-01"),
    ("inflation_hiking_2021_2022", "2021-01-01", "2023-01-01"),
    ("normalization_2023_2024", "2023-01-01", "2025-01-01"),
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _episode_label(ts: pd.Timestamp) -> str:
    for label, start, end in EPISODES:
        if pd.Timestamp(start) <= ts < pd.Timestamp(end):
            return label
    return "outside_window"


def _coerce_rows(attribution: dict) -> pd.DataFrame:
    rows = pd.DataFrame(attribution["rows"])
    rows["exit_timestamp"] = pd.to_datetime(rows["exit_timestamp"])
    rows["entry_timestamp"] = pd.to_datetime(rows["entry_timestamp"])
    rows["episode_bucket"] = rows["exit_timestamp"].map(_episode_label)
    return rows


def _episode_summary(rows: pd.DataFrame) -> dict[str, dict[str, object]]:
    engine_b = rows[rows["engine"] == "ENGINE_B"].copy()
    out: dict[str, dict[str, object]] = {}
    for label, *_ in EPISODES:
        bucket = engine_b[engine_b["episode_bucket"] == label].copy()
        if bucket.empty:
            out[label] = {
                "engine_b_net_pnl": 0.0,
                "trade_count": 0,
                "phase3_trade_count": 0,
                "phase3_pnl_fraction": 0.0,
                "top_instruments": [],
            }
            continue
        symbol_pnl = (
            bucket.groupby("symbol", as_index=False)["pnl"]
            .sum()
            .sort_values("pnl", ascending=False)
        )
        phase3 = bucket[bucket["max_core_phase"] >= 3]
        total_pnl = float(bucket["pnl"].sum())
        phase3_pnl = float(phase3["pnl"].sum())
        pnl_fraction = 0.0 if abs(total_pnl) < 1e-9 else phase3_pnl / total_pnl
        out[label] = {
            "engine_b_net_pnl": total_pnl,
            "trade_count": int(len(bucket)),
            "phase3_trade_count": int(len(phase3)),
            "phase3_pnl_fraction": float(pnl_fraction),
            "top_instruments": [
                {"symbol": str(row.symbol), "pnl": float(row.pnl)}
                for row in symbol_pnl.head(3).itertuples()
            ],
        }
    return out


def _comparison_row(name: str, final_obj: dict, diagnostics: dict) -> dict[str, object]:
    metrics = final_obj["metrics"]
    return {
        "variant": name,
        "cagr": float(metrics["annualized_return"]),
        "sharpe": float(metrics["sharpe_ratio"]),
        "max_dd": float(metrics["max_drawdown"]),
        "profit_factor": float(metrics["profit_factor"]),
        "phase3_ratio": float(diagnostics.get("engine_b_phase3_ratio", 0.0)),
        "overlay_weight": float(diagnostics.get("overlay_weight_mean", 0.0)),
    }


def render_markdown(summary: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("# Macro Barbell Experiment Analysis")
    lines.append("")
    lines.append("## Variant Comparison")
    lines.append("")
    lines.append("| Variant | CAGR | Sharpe | MaxDD | PF | Phase3 Ratio | Overlay Weight |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in summary["comparison"]:
        lines.append(
            "| {variant} | {cagr:.4%} | {sharpe:.4f} | {max_dd:.4%} | {profit_factor:.4f} | {phase3_ratio:.4%} | {overlay_weight:.4%} |".format(
                **row
            )
        )
    for variant, info in summary["variants"].items():
        lines.append("")
        lines.append(f"## {variant}")
        lines.append("")
        for episode, episode_info in info["episode_summary"].items():
            top = ", ".join(
                f"{item['symbol']} ({item['pnl']:.2f})" for item in episode_info["top_instruments"]
            ) or "none"
            lines.append(
                f"- `{episode}`: Engine B PnL `{episode_info['engine_b_net_pnl']:.2f}`, trades `{episode_info['trade_count']}`, "
                f"Phase 3 trades `{episode_info['phase3_trade_count']}`, Phase 3 PnL fraction `{episode_info['phase3_pnl_fraction']:.2%}`, top instruments `{top}`"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze macro barbell experiment artifacts.")
    parser.add_argument(
        "--reports-dir",
        type=str,
        default="reports/market_wizards",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default="macro_barbell_experiment_analysis",
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    summary: dict[str, object] = {"variants": {}, "comparison": []}
    for variant, (final_name, diag_name, attr_name) in VARIANT_FILES.items():
        final_obj = _load_json(reports_dir / final_name)
        diagnostics = _load_json(reports_dir / diag_name)
        attribution = _load_json(reports_dir / attr_name)
        rows = _coerce_rows(attribution)
        episode_summary = _episode_summary(rows)
        summary["variants"][variant] = {
            "episode_summary": episode_summary,
            "diagnostics": diagnostics,
            "metrics": final_obj["metrics"],
        }
        summary["comparison"].append(_comparison_row(variant, final_obj, diagnostics))

    json_path = reports_dir / f"{args.output_prefix}.json"
    md_path = reports_dir / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
