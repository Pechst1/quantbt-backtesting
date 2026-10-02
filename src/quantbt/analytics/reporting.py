from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

from quantbt.analytics.performance import PerformanceReport


class TearSheetReporter:
    def __init__(self, output_dir: str | Path = "reports") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, report: PerformanceReport, run_name: str = "backtest") -> dict[str, str]:
        metrics_path = self.output_dir / f"{run_name}_metrics.json"
        equity_path = self.output_dir / f"{run_name}_equity.csv"
        figure_path = self.output_dir / f"{run_name}_tearsheet.png"

        with metrics_path.open("w", encoding="utf-8") as f:
            json.dump(report.metrics, f, indent=2)

        report.equity_curve.to_csv(equity_path, header=["equity"])

        fig, axes = plt.subplots(3, 1, figsize=(14, 14), constrained_layout=True)

        axes[0].plot(report.equity_curve.index, report.equity_curve.values, color="#1d4ed8", linewidth=1.8)
        axes[0].set_title("Equity Curve")
        axes[0].set_ylabel("Equity")
        axes[0].grid(alpha=0.3)

        axes[1].fill_between(
            report.drawdown.index,
            report.drawdown.values,
            0,
            color="#dc2626",
            alpha=0.25,
        )
        axes[1].plot(report.drawdown.index, report.drawdown.values, color="#991b1b", linewidth=1.2)
        axes[1].set_title("Drawdown Curve")
        axes[1].set_ylabel("Drawdown")
        axes[1].grid(alpha=0.3)

        if report.monthly_returns.empty:
            axes[2].text(0.5, 0.5, "No monthly return data", ha="center", va="center")
            axes[2].axis("off")
        else:
            sns.heatmap(
                report.monthly_returns,
                cmap="RdYlGn",
                center=0.0,
                annot=True,
                fmt=".1%",
                linewidths=0.3,
                cbar_kws={"label": "Monthly Return"},
                ax=axes[2],
            )
            axes[2].set_title("Monthly Returns Heatmap")
            axes[2].set_xlabel("Month")
            axes[2].set_ylabel("Year")

        fig.savefig(figure_path, dpi=160)
        plt.close(fig)

        return {
            "metrics_json": str(metrics_path),
            "equity_csv": str(equity_path),
            "tearsheet_png": str(figure_path),
        }

