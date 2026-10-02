from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategies import (
    AtrBreakoutStrategy,
    BollingerMeanReversionStrategy,
    CciMeanReversionStrategy,
    EmaCrossStrategy,
    MacdSignalStrategy,
    Momentum121Strategy,
    RsiMeanReversionStrategy,
    SmaCrossStrategy,
    StochasticOscillatorStrategy,
    TurtleBreakoutStrategy,
    ZScoreMeanReversionStrategy,
)


# Large global equity basket across North America, Europe, Asia-Pacific and LatAm.
DEFAULT_BASKET = [
    # US
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "META",
    "NVDA",
    "JPM",
    "JNJ",
    "XOM",
    "PG",
    "KO",
    "WMT",
    "IBM",
    "CVX",
    "BAC",
    "PFE",
    "MRK",
    "DIS",
    "HD",
    "MCD",
    # Canada
    "RY.TO",
    "TD.TO",
    "ENB.TO",
    "CNR.TO",
    "BNS.TO",
    # UK
    "HSBA.L",
    "BP.L",
    "SHEL.L",
    "ULVR.L",
    "AZN.L",
    "RIO.L",
    "VOD.L",
    "BARC.L",
    # Europe
    "SAP.DE",
    "SIE.DE",
    "AIR.PA",
    "OR.PA",
    "SAN.PA",
    "MC.PA",
    "ASML.AS",
    "NOVN.SW",
    "NESN.SW",
    "ROG.SW",
    "BAYN.DE",
    # Japan
    "7203.T",
    "6758.T",
    "9432.T",
    "9983.T",
    "8306.T",
    "8058.T",
    # Hong Kong / China listings
    "0700.HK",
    "0005.HK",
    "1299.HK",
    "9988.HK",
    "2318.HK",
    # India
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ITC.NS",
    # Australia
    "BHP.AX",
    "CBA.AX",
    "CSL.AX",
    "WBC.AX",
    "NAB.AX",
    # Brazil
    "VALE3.SA",
    "PETR4.SA",
    "ITUB4.SA",
    # Korea / Taiwan
    "005930.KS",
    "000660.KS",
    "2330.TW",
]


@dataclass(slots=True)
class SuiteResult:
    strategy: str
    symbols_requested: int
    symbols_used: int
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    win_rate: float
    profit_factor: float
    num_closed_trades: float
    submitted_orders: int
    fills: int
    margin_calls: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a strategy benchmark suite on a broad global equity basket."
    )
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_BASKET))
    parser.add_argument("--start", type=str, default="1975-01-01")
    parser.add_argument("--end", type=str, default="2025-01-01")
    parser.add_argument("--interval", type=str, default="1d")
    parser.add_argument("--initial-cash", type=float, default=250_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.01)
    parser.add_argument("--output-dir", type=str, default="reports/strategy_suite")
    return parser.parse_args()


def strategy_builders() -> list[tuple[str, Callable[[list[str]], object]]]:
    return [
        ("sma_cross_20_100", lambda symbols: SmaCrossStrategy(symbols=symbols, short_window=20, long_window=100)),
        ("ema_cross_12_26", lambda symbols: EmaCrossStrategy(symbols=symbols, fast_window=12, slow_window=26)),
        ("rsi_mean_reversion", lambda symbols: RsiMeanReversionStrategy(symbols=symbols, rsi_period=14)),
        (
            "bollinger_mean_reversion",
            lambda symbols: BollingerMeanReversionStrategy(symbols=symbols, window=20, std_multiplier=2.0),
        ),
        ("macd_signal_12_26_9", lambda symbols: MacdSignalStrategy(symbols=symbols)),
        ("stochastic_14_3", lambda symbols: StochasticOscillatorStrategy(symbols=symbols, lookback=14, signal_window=3)),
        ("cci_mean_reversion_20", lambda symbols: CciMeanReversionStrategy(symbols=symbols, period=20)),
        ("momentum_12_1", lambda symbols: Momentum121Strategy(symbols=symbols, lookback=252, skip=21)),
        ("atr_breakout_14", lambda symbols: AtrBreakoutStrategy(symbols=symbols, atr_period=14, atr_multiplier=1.0)),
        ("turtle_55_20", lambda symbols: TurtleBreakoutStrategy(symbols=symbols, entry_window=55, exit_window=20)),
        ("zscore_mean_reversion_20", lambda symbols: ZScoreMeanReversionStrategy(symbols=symbols, window=20, entry_z=1.5)),
    ]


def run_single_strategy(
    *,
    strategy_name: str,
    strategy_builder: Callable[[list[str]], object],
    symbols: list[str],
    start: datetime,
    end: datetime,
    interval: str,
    initial_cash: float,
    risk_fraction: float,
) -> SuiteResult:
    requested_count = len(symbols)
    data_handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=symbols,
            start=start,
            end=end,
            interval=interval,
            adjust_prices=True,
            strict_symbols=False,
        )
    )
    active_symbols = data_handler.active_symbols
    strategy = strategy_builder(active_symbols)
    portfolio = Portfolio(
        initial_cash=initial_cash,
        leverage=2.0,
        maintenance_margin_ratio=0.25,
        position_sizer=FixedFractionalSizer(risk_fraction=risk_fraction, max_allocation=0.10),
    )
    execution = SimulatedExecutionHandler(
        config=ExecutionConfig(
            slippage_bps=2.0,
            spread_bps=5.0,
            commission_fixed=0.0,
            commission_pct=0.0005,
        )
    )
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,  # type: ignore[arg-type]
        portfolio=portfolio,
        execution_handler=execution,
    )
    result = engine.run(run_name=strategy_name)
    metrics = result.report.metrics
    return SuiteResult(
        strategy=strategy_name,
        symbols_requested=requested_count,
        symbols_used=len(active_symbols),
        annualized_return=float(metrics["annualized_return"]),
        sharpe_ratio=float(metrics["sharpe_ratio"]),
        sortino_ratio=float(metrics["sortino_ratio"]),
        max_drawdown=float(metrics["max_drawdown"]),
        calmar_ratio=float(metrics["calmar_ratio"]),
        win_rate=float(metrics["win_rate"]),
        profit_factor=float(metrics["profit_factor"]),
        num_closed_trades=float(metrics["num_closed_trades"]),
        submitted_orders=result.submitted_orders,
        fills=result.fills,
        margin_calls=result.margin_calls,
    )


def main() -> None:
    args = parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[SuiteResult] = []
    for name, builder in strategy_builders():
        print(f"[suite] running {name} on {len(symbols)} symbols...")
        result = run_single_strategy(
            strategy_name=name,
            strategy_builder=builder,
            symbols=symbols,
            start=start,
            end=end,
            interval=args.interval,
            initial_cash=args.initial_cash,
            risk_fraction=args.risk_fraction,
        )
        results.append(result)

    frame = pd.DataFrame([asdict(result) for result in results]).sort_values(
        by="sharpe_ratio",
        ascending=False,
    )
    csv_path = output_dir / "summary.csv"
    json_path = output_dir / "summary.json"
    frame.to_csv(csv_path, index=False)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump([asdict(result) for result in results], f, indent=2)

    display_cols = [
        "strategy",
        "symbols_used",
        "annualized_return",
        "sharpe_ratio",
        "max_drawdown",
        "calmar_ratio",
        "profit_factor",
        "num_closed_trades",
    ]
    print("\n=== Strategy Suite Summary (sorted by sharpe_ratio) ===")
    print(frame[display_cols].to_string(index=False))
    print(f"\nArtifacts:\n- {csv_path}\n- {json_path}")


if __name__ == "__main__":
    main()
