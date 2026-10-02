from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from quantbt.altdata import (
    CSVBorrowDataSource,
    CSVCorporateActionsDataSource,
    CSVEarningsDataSource,
    CSVFactorDataSource,
    CSVFundamentalsDataSource,
    CSVIndexMembershipDataSource,
    HistoricalUniverseBuilder,
    KenFrenchFactorDataSource,
    StaticBorrowDataSource,
    YahooCorporateActionsDataSource,
    YahooEarningsDataSource,
)
from quantbt.analytics.reporting import TearSheetReporter
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategies import (
    AntiLotteryShortStrategy,
    CapitulationBuyStrategy,
    PEADInconsistencyAvoidanceStrategy,
)


DEFAULT_SP500_MEMBERSHIP_CSV = (
    Path(__file__).resolve().parents[1] / "data/index_membership/sp500_membership_full.csv"
)


def _normalize_index_name(name: str) -> str:
    key = name.upper().replace("-", "").replace(" ", "")
    aliases = {
        "SP500": "SP500",
        "S&P500": "SP500",
        "SNP500": "SP500",
        "SP1500": "SP1500",
        "S&P1500": "SP1500",
        "SNP1500": "SP1500",
        "RUSSELL2000": "RUSSELL2000",
        "RUT2000": "RUSSELL2000",
        "R2K": "RUSSELL2000",
    }
    return aliases.get(key, key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run behavioral strategies with PiT data sources.")
    parser.add_argument("--strategy", type=str, choices=["pead", "anti_lottery", "capitulation"], required=True)
    parser.add_argument("--symbols", type=str, required=True)
    parser.add_argument("--start", type=str, default="2018-01-01")
    parser.add_argument("--end", type=str, default="2025-01-01")
    parser.add_argument("--interval", type=str, default="1d")
    parser.add_argument("--initial-cash", type=float, default=250_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.01)
    parser.add_argument("--output-dir", type=str, default="reports/behavioral")
    parser.add_argument("--earnings-csv", type=str, default="")
    parser.add_argument("--fundamentals-csv", type=str, default="")
    parser.add_argument("--corporate-actions-csv", type=str, default="")
    parser.add_argument("--borrow-csv", type=str, default="")
    parser.add_argument("--factor-csv", type=str, default="")
    parser.add_argument("--index-membership-csv", type=str, default="")
    parser.add_argument("--universe", type=str, default="", help="sp500 | sp1500 | russell2000 | custom index name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)

    data_handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=symbols,
            start=start,
            end=end,
            interval=args.interval,
            adjust_prices=True,
        )
    )

    if args.borrow_csv:
        borrow_source = CSVBorrowDataSource(args.borrow_csv)
    else:
        borrow_source = StaticBorrowDataSource(annualized_fee=0.03, hard_to_borrow_symbols=set())

    universe = None
    membership_csv = args.index_membership_csv.strip()
    if args.universe:
        if not membership_csv and _normalize_index_name(args.universe) == "SP500" and DEFAULT_SP500_MEMBERSHIP_CSV.exists():
            membership_csv = str(DEFAULT_SP500_MEMBERSHIP_CSV)
            print(f"Using default full SP500 membership CSV: {membership_csv}")
        if not membership_csv:
            raise ValueError(
                "Universe enforcement requested but no index membership CSV was provided. "
                "Provide --index-membership-csv or run "
                "`python examples/load_index_membership_data.py` for SP500 defaults."
            )
        membership_source = CSVIndexMembershipDataSource(membership_csv)
        universe = HistoricalUniverseBuilder(membership_source).for_index(args.universe)

    if args.strategy == "pead":
        if args.earnings_csv:
            earnings_source = CSVEarningsDataSource(args.earnings_csv)
        else:
            earnings_source = YahooEarningsDataSource(symbols=symbols)
        event_count = len(getattr(earnings_source, "_events", []))
        if event_count == 0:
            raise ValueError(
                "No earnings events were loaded for PEAD. "
                "Install lxml (`python -m pip install lxml`) for Yahoo earnings scraping "
                "or provide --earnings-csv with PiT earnings events."
            )
        strategy = PEADInconsistencyAvoidanceStrategy(
            symbols=symbols,
            earnings_source=earnings_source,
            sue_threshold=2.0,
            hold_days=60,
        )
    elif args.strategy == "anti_lottery":
        if args.fundamentals_csv:
            fundamentals_source = CSVFundamentalsDataSource(args.fundamentals_csv)
        else:
            raise ValueError("anti_lottery strategy requires --fundamentals-csv for PiT net income.")
        if args.factor_csv:
            factor_source = CSVFactorDataSource(args.factor_csv)
        else:
            factor_source = KenFrenchFactorDataSource()

        if fundamentals_source is None:
            raise ValueError("anti_lottery strategy requires --fundamentals-csv for PiT net income.")
        strategy = AntiLotteryShortStrategy(
            symbols=symbols,
            fundamentals_source=fundamentals_source,
            factor_source=factor_source,
            rebalance_top_pct=0.05,
            lookback_days=30,
            min_price=5.0,
            stop_loss_pct=0.20,
        )
    else:
        if args.corporate_actions_csv:
            actions_source = CSVCorporateActionsDataSource(args.corporate_actions_csv)
        else:
            actions_source = YahooCorporateActionsDataSource(symbols=symbols)
        strategy = CapitulationBuyStrategy(
            symbols=symbols,
            corporate_actions_source=actions_source,
            drop_threshold=-0.15,
            volume_multiplier=3.0,
            rsi_entry=20.0,
            rsi_exit=50.0,
            max_holding_days=10,
        )

    portfolio = Portfolio(
        initial_cash=args.initial_cash,
        leverage=2.0,
        maintenance_margin_ratio=0.25,
        position_sizer=FixedFractionalSizer(risk_fraction=args.risk_fraction),
        max_gross_exposure=2.0,
        max_net_exposure=1.0,
        hard_stop_loss_pct=0.20,
    )

    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(
            slippage_bps=2.0,
            spread_bps=6.0,
            volume_impact_bps=8.0,
            commission_fixed=0.0,
            commission_pct=0.0005,
            sec_fee_rate=0.000008,
            exchange_fee_per_share=0.0002,
        ),
        borrow_source=borrow_source,
    )
    reporter = TearSheetReporter(output_dir=args.output_dir)

    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution,
        reporter=reporter,
        universe=universe,
    )
    result = engine.run(run_name=f"{args.strategy}_{'_'.join(symbols)}")

    print("=== Backtest Metrics ===")
    for key, value in result.report.metrics.items():
        print(f"{key:>24}: {value:.6f}")
    print("\n=== Engine Stats ===")
    print(f"submitted_orders          : {result.submitted_orders}")
    print(f"fills                     : {result.fills}")
    print(f"rejected_orders           : {result.rejected_orders}")
    print(f"risk_rejections           : {result.risk_rejections}")
    print(f"universe_rejections       : {result.universe_rejections}")
    print(f"borrow_fees_paid          : {result.borrow_fees_paid:.6f}")
    print(f"margin_calls              : {result.margin_calls}")
    print("\n=== Artifacts ===")
    for name, path in result.artifacts.items():
        print(f"{name:>24}: {path}")


if __name__ == "__main__":
    main()
