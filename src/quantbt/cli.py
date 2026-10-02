from __future__ import annotations

import argparse
from datetime import UTC, datetime

from quantbt.analytics.reporting import TearSheetReporter
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategies.sma_cross import SmaCrossStrategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Event-driven multi-asset backtesting engine")
    parser.add_argument("--symbols", type=str, default="AAPL,MSFT")
    parser.add_argument("--start", type=str, default="2018-01-01")
    parser.add_argument("--end", type=str, default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--interval", type=str, default="1d")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--short-window", type=int, default=20)
    parser.add_argument("--long-window", type=int, default=100)
    parser.add_argument("--risk-fraction", type=float, default=0.02)
    parser.add_argument("--hard-stop-loss-pct", type=float, default=0.20)
    parser.add_argument("--max-gross-exposure", type=float, default=2.0)
    parser.add_argument("--max-net-exposure", type=float, default=1.0)
    parser.add_argument("--output-dir", type=str, default="reports")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]

    handler_cfg = DataHandlerConfig(
        symbols=symbols,
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        interval=args.interval,
        adjust_prices=True,
    )

    data_handler = PublicOHLCVDataHandler(config=handler_cfg)
    strategy = SmaCrossStrategy(
        symbols=symbols,
        short_window=args.short_window,
        long_window=args.long_window,
        stop_loss_pct=0.03,
        take_profit_pct=0.06,
    )
    portfolio = Portfolio(
        initial_cash=args.initial_cash,
        leverage=2.0,
        maintenance_margin_ratio=0.25,
        position_sizer=FixedFractionalSizer(risk_fraction=args.risk_fraction),
        max_gross_exposure=args.max_gross_exposure,
        max_net_exposure=args.max_net_exposure,
        hard_stop_loss_pct=args.hard_stop_loss_pct,
    )
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(
            slippage_bps=2.0,
            spread_bps=5.0,
            volume_impact_bps=8.0,
            commission_fixed=0.0,
            commission_pct=0.0005,
            sec_fee_rate=0.000008,
            exchange_fee_per_share=0.0002,
        )
    )
    reporter = TearSheetReporter(output_dir=args.output_dir)

    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution,
        reporter=reporter,
    )
    result = engine.run(run_name=f"{'_'.join(symbols)}_{args.interval}")

    print("=== Backtest Metrics ===")
    for key, value in result.report.metrics.items():
        print(f"{key:>20}: {value:.6f}")
    print("\n=== Artifacts ===")
    for name, path in result.artifacts.items():
        print(f"{name:>20}: {path}")
    print("\n=== Engine Stats ===")
    print(f"submitted_orders      : {result.submitted_orders}")
    print(f"fills                 : {result.fills}")
    print(f"margin_calls          : {result.margin_calls}")
    print(f"rejected_orders       : {result.rejected_orders}")
    print(f"risk_rejections       : {result.risk_rejections}")
    print(f"universe_rejections   : {result.universe_rejections}")
    print(f"borrow_fees_paid      : {result.borrow_fees_paid:.6f}")


if __name__ == "__main__":
    main()
