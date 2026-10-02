from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_RUN_NAME = "global_macro_barbell_research_reversal_rates_yieldfutures"
RATE_SYMBOLS = {"2YY=F", "5YY=F", "10Y=F", "30Y=F", "^FVX", "^TNX", "^TYX", "ZT=F", "ZF=F", "ZN=F", "ZB=F", "IEF", "TLT"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(group: pd.DataFrame) -> dict[str, float]:
    if group.empty:
        return {"trades": 0.0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0}
    wins = group.loc[group["pnl"] > 0, "pnl"].sum()
    losses = -group.loc[group["pnl"] < 0, "pnl"].sum()
    return {
        "trades": float(len(group)),
        "net_pnl": float(group["pnl"].sum()),
        "win_rate": float((group["pnl"] > 0).mean()),
        "profit_factor": float("inf") if losses <= 1e-12 and wins > 0 else float(wins / max(losses, 1e-12)),
    }


def _top_symbols(group: pd.DataFrame, n: int = 5) -> list[dict[str, float | str]]:
    if group.empty:
        return []
    by_symbol = group.groupby("symbol", as_index=False)["pnl"].sum().sort_values("pnl", ascending=False)
    return [{"symbol": str(row.symbol), "pnl": float(row.pnl)} for row in by_symbol.head(n).itertuples()]


def analyze_variant(label: str, folder: Path, run_name: str) -> dict:
    metrics = _load_json(folder / f"{run_name}_metrics.json")
    diagnostics = _load_json(folder / f"{run_name}_diagnostics.json")
    attribution = _load_json(folder / f"{run_name}_attribution.json")
    rows = pd.DataFrame(attribution["rows"])
    if not rows.empty:
        rows["episode_exit"] = rows["episode_exit"].fillna("all_other_periods")

    rates = rows[(rows["engine"] == "RATES_CURVE") | (rows["symbol"].isin(RATE_SYMBOLS))].copy()
    episode_summary = {}
    for episode in sorted(rows["episode_exit"].dropna().unique()):
        bucket = rows[rows["episode_exit"] == episode]
        rates_bucket = rates[rates["episode_exit"] == episode]
        episode_summary[str(episode)] = {
            "all": _summary(bucket),
            "rates": _summary(rates_bucket),
            "rates_top_symbols": _top_symbols(rates_bucket, n=3),
        }

    return {
        "label": label,
        "folder": str(folder),
        "metrics": metrics,
        "diagnostics": {
            "engine_b_phase3_ratio": diagnostics.get("engine_b_phase3_ratio", 0.0),
            "rates_curve_active_ratio": diagnostics.get("rates_curve_active_ratio", 0.0),
            "rates_curve_gross_mean": diagnostics.get("rates_curve_gross_mean", 0.0),
            "rates_curve_futures_gross_mean": diagnostics.get("rates_curve_futures_gross_mean", 0.0),
            "rates_curve_proxy_gross_mean": diagnostics.get("rates_curve_proxy_gross_mean", 0.0),
            "rates_curve_zero_contract_fallback_total": diagnostics.get("rates_curve_zero_contract_fallback_total", 0.0),
            "rates_curve_trend_filter_total": diagnostics.get("rates_curve_trend_filter_total", 0.0),
        },
        "by_engine": attribution.get("by_engine", {}),
        "by_symbol": attribution.get("by_symbol", {}),
        "episode_summary": episode_summary,
    }


def render_markdown(summary: dict) -> str:
    lines = [
        "# Rates Budget Robustness",
        "",
        "## Variant Comparison",
        "",
        "| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades | Rates PnL | Rates PF | Rates Gross |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in summary["variants"]:
        metrics = variant["metrics"]
        rates = variant["by_engine"].get("RATES_CURVE", {})
        diag = variant["diagnostics"]
        lines.append(
            "| {label} | {cagr:.2%} | {sharpe:.3f} | {maxdd:.2%} | {calmar:.3f} | {pf:.3f} | {trades:.0f} | {rates_pnl:,.0f} | {rates_pf:.3f} | {rates_gross:.2%} |".format(
                label=variant["label"],
                cagr=float(metrics["annualized_return"]),
                sharpe=float(metrics["sharpe_ratio"]),
                maxdd=float(metrics["max_drawdown"]),
                calmar=float(metrics["calmar_ratio"]),
                pf=float(metrics["profit_factor"]),
                trades=float(metrics["num_closed_trades"]),
                rates_pnl=float(rates.get("net_pnl", 0.0)),
                rates_pf=float(rates.get("profit_factor", 0.0)),
                rates_gross=float(diag.get("rates_curve_gross_mean", 0.0)),
            )
        )
    for variant in summary["variants"]:
        lines.extend(["", f"## {variant['label']} Episode Attribution", ""])
        lines.append("| Episode | Total PnL | Rates PnL | Rates PF | Top Rates Symbols |")
        lines.append("|---|---:|---:|---:|---|")
        for episode, info in variant["episode_summary"].items():
            top = ", ".join(f"{item['symbol']} {item['pnl']:,.0f}" for item in info["rates_top_symbols"]) or "none"
            lines.append(
                "| {episode} | {total:,.0f} | {rates:,.0f} | {pf:.3f} | {top} |".format(
                    episode=episode,
                    total=float(info["all"]["net_pnl"]),
                    rates=float(info["rates"]["net_pnl"]),
                    pf=float(info["rates"]["profit_factor"]),
                    top=top,
                )
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare rates/curve budget variants by episode.")
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        help="Variant in label=reports/path form. May be passed multiple times.",
    )
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--output-json", default="reports/market_wizards/rates_budget_robustness.json")
    parser.add_argument("--output-md", default="reports/market_wizards/rates_budget_robustness.md")
    args = parser.parse_args()

    variants = []
    for raw in args.variant:
        label, folder = raw.split("=", 1)
        variants.append(analyze_variant(label, Path(folder), args.run_name))
    summary = {"run_name": args.run_name, "variants": variants}

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(summary), encoding="utf-8")
    print(f"Wrote {output_json}")
    print(f"Wrote {output_md}")


if __name__ == "__main__":
    main()
