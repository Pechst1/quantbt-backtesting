from __future__ import annotations

from datetime import datetime

from quantbt.analytics.reporting import TearSheetReporter
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import RiskBasedSizer
from quantbt.strategies.sma_cross import SmaCrossStrategy


def main() -> None:
    symbols = ["AAPL", "MSFT", "SPY"]
    data_handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=symbols,
            start=datetime(2018, 1, 1),
            end=datetime(2025, 1, 1),
            interval="1d",
            adjust_prices=True,
        )
    )
    strategy = SmaCrossStrategy(
        symbols=symbols,
        short_window=20,
        long_window=100,
        stop_loss_pct=0.03,
        take_profit_pct=0.06,
    )
    portfolio = Portfolio(
        initial_cash=150_000,
        leverage=2.0,
        maintenance_margin_ratio=0.25,
        position_sizer=RiskBasedSizer(risk_per_trade=0.01),
    )
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(
            slippage_bps=2.0,
            spread_bps=5.0,
            commission_fixed=0.5,
            commission_pct=0.0005,
        )
    )
    reporter = TearSheetReporter(output_dir="reports")

    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution,
        reporter=reporter,
    )
    result = engine.run(run_name="sma_cross_example")

    print("Metrics:")
    for key, value in result.report.metrics.items():
        print(f"- {key}: {value:.6f}")
    print("\nArtifacts:")
    for name, path in result.artifacts.items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()

