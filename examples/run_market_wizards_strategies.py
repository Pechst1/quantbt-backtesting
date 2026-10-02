from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from quantbt.altdata import (
    CSVCatalystDataSource,
    CSVEarningsDataSource,
    CSVFundamentalsDataSource,
    CSVFuturesCurveDataSource,
    CSVMacroDataSource,
    CSVMacroSurpriseDataSource,
    CSVSecurityMasterDataSource,
    CSVVolatilityDataSource,
)
from quantbt.analytics.reporting import TearSheetReporter
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine, BacktestResult
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.portfolio.sizing import FixedFractionalSizer
from quantbt.strategies import (
    BarbellMacroWizardStrategy,
    EventMomentumWizardStrategy,
    LongVolatilityConvexityWizardStrategy,
    PairSpec,
    RelativeValueStatArbWizardStrategy,
    StockLeadershipBreakoutWizardStrategy,
    TopDownGlobalMacroWizardStrategy,
    TrendBreakoutWizardStrategy,
    TrendPullbackReversalWizardStrategy,
    ValueCatalystWizardStrategy,
)


DATA_ROOT = Path(__file__).resolve().parents[1] / "data/market_wizards"
DEFAULT_MACRO_PUBLIC = DATA_ROOT / "macro_regimes_public.csv"
DEFAULT_MACRO_LEGACY = DATA_ROOT / "macro_regimes.csv"
DEFAULT_FUTURES_CURVE = DATA_ROOT / "futures_curve_signals.csv"
DEFAULT_MACRO_SURPRISES = DATA_ROOT / "macro_surprises_public.csv"

FUTURES_MULTIPLIERS = {
    "CL=F": 1_000.0,   # WTI crude: 1,000 barrels.
    "GC=F": 100.0,     # COMEX gold: 100 troy ounces.
    "HG=F": 25_000.0,  # COMEX copper: 25,000 pounds.
    "ZC=F": 5_000.0,   # CBOT corn: 5,000 bushels.
    "ZT=F": 2_000.0,   # 2Y Treasury futures: $2,000 per point.
    "ZF=F": 1_000.0,   # 5Y Treasury futures: $1,000 per point.
    "ZN=F": 1_000.0,   # 10Y Treasury futures: $1,000 per point.
    "ZB=F": 1_000.0,   # 30Y Treasury futures: $1,000 per point.
    "2YY=F": 1_000.0,  # CME Yield futures: $1,000 per yield-index point ($10 DV01).
    "^FVX": 1_000.0,   # Yahoo Treasury yield indices used as synthetic $10-DV01 yield proxies.
    "^TNX": 1_000.0,
    "^TYX": 1_000.0,
}
FUTURES_SYMBOLS = set(FUTURES_MULTIPLIERS)
# Yahoo currently exposes 2YY=F, but the 5Y/10Y/30Y yield-future tickers are
# not consistently available. Use public Treasury-yield indices for those tenors.
RATE_YIELD_FUTURES = {"2YY=F"}
RATE_YIELD_INDEX_PROXIES = {"^FVX", "^TNX", "^TYX"}


@dataclass(slots=True)
class StrategyRunSpec:
    name: str
    symbols: list[str]
    strategy: object
    leverage: float
    max_gross: float
    max_net: float
    hard_stop: float
    precise: bool
    note: str
    optional_symbols: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Market Wizards-inspired strategy backtests.")
    parser.add_argument(
        "--strategy",
        type=str,
        default="all",
        choices=[
            "all",
            "trend_breakout",
            "trend_pullback",
            "leader_breakout",
            "event_momentum",
            "value_catalyst",
            "global_macro",
            "global_macro_barbell",
            "relative_value",
            "convexity",
        ],
    )
    parser.add_argument("--start", type=str, default="2020-01-01")
    parser.add_argument("--end", type=str, default="2025-01-01")
    parser.add_argument("--interval", type=str, default="1d")
    parser.add_argument("--initial-cash", type=float, default=250_000.0)
    parser.add_argument("--output-dir", type=str, default="reports/market_wizards")
    parser.add_argument(
        "--macro-csv",
        type=str,
        default="",
        help="Optional macro snapshot CSV. Defaults to macro_regimes_public.csv when available, otherwise macro_regimes.csv.",
    )
    parser.add_argument(
        "--macro-inputs-are-zscores",
        action="store_true",
        help="Treat macro snapshot factor columns as already standardized z-scores instead of z-scoring them again.",
    )
    parser.add_argument(
        "--futures-curve-csv",
        type=str,
        default="",
        help="Optional point-in-time futures curve signal CSV used by commodity futures sleeves.",
    )
    parser.add_argument(
        "--macro-surprise-csv",
        type=str,
        default="",
        help="Optional point-in-time macro surprise event CSV for the macro surprise layer.",
    )
    parser.add_argument("--no-report", action="store_true", help="Skip tear-sheet generation.")
    parser.add_argument(
        "--barbell-velocity-layer",
        action="store_true",
        help="Enable the experimental Step 3 macro-velocity layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-phase-stop-layer",
        action="store_true",
        help="Enable the experimental Step 4 phase-based stop layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-engine-a-reallocation-layer",
        action="store_true",
        help="Enable the experimental Step A capital reallocation layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-asymmetric-phase-layer",
        action="store_true",
        help="Enable the experimental Step B asymmetric crisis-expression phase timing layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-extreme-concentration-layer",
        action="store_true",
        help="Enable the experimental Step D extreme-regime concentration layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-phase3-profit-rate-layer",
        action="store_true",
        help="Enable the experimental profit-rate-based Phase 2->3 promotion layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-overlay-routing-layer",
        action="store_true",
        help="Enable the experimental overlay capital-routing layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-focused-reallocation-layer",
        action="store_true",
        help="Enable the experimental Step A + Step D focused reallocation layer for the barbell strategy.",
    )
    parser.add_argument(
        "--barbell-overlay-off",
        action="store_true",
        help="Disable the transmission-lag overlay explicitly.",
    )
    parser.add_argument(
        "--barbell-research-baseline",
        action="store_true",
        help="Promote the current research baseline: extreme concentration + Phase3 profit-rate + overlay off.",
    )
    parser.add_argument(
        "--barbell-current-best",
        action="store_true",
        help=(
            "Run the current best-yield stack: research baseline + liquidation reversal + carry crash-risk "
            "+ rates/yield-futures sleeve + commodity-futures satellite."
        ),
    )
    parser.add_argument(
        "--barbell-market-evidence-layer",
        action="store_true",
        help="Let volatility-normalized market trends determine direction while macro data remains diagnostic.",
    )
    parser.add_argument(
        "--barbell-robust-redesign",
        action="store_true",
        help="Run current-best sleeves with market-evidence direction and cheap Phase-1 discovery probes.",
    )
    parser.add_argument(
        "--barbell-disable-engine-b-core",
        action="store_true",
        help="Ablate the directional Engine B core while retaining independent carry, rates, trend, commodity, and reversal sleeves.",
    )
    parser.add_argument(
        "--barbell-cheap-discovery-layer",
        action="store_true",
        help="Reduce unconfirmed Engine B Phase-1 probes to 15% size while preserving Phase-2/3 sizing.",
    )
    parser.add_argument(
        "--barbell-convex-dislocation-layer",
        action="store_true",
        help="Enable the Engine B convex dislocation proxy sleeve. This trades underlying proxies, not options.",
    )
    parser.add_argument(
        "--barbell-commodity-futures-layer",
        action="store_true",
        help="Use Yahoo continuous commodity futures proxies instead of USO/DBC in Engine B.",
    )
    parser.add_argument(
        "--barbell-commodity-futures-sleeve-layer",
        action="store_true",
        help="Add commodity futures as a separate trend-confirmed satellite sleeve without replacing USO/DBC.",
    )
    parser.add_argument(
        "--barbell-commodity-futures-symbols",
        type=str,
        default="HG=F",
        help="Comma-separated commodity futures satellite symbols used by --barbell-commodity-futures-sleeve-layer.",
    )
    parser.add_argument(
        "--barbell-futures-expression-layer",
        action="store_true",
        help="Add futures-style commodity/rates expressions with contract multipliers and margin-aware notional.",
    )
    parser.add_argument(
        "--barbell-rates-curve-layer",
        action="store_true",
        help="Add a dedicated rates/curve sleeve using Treasury futures proxies when available.",
    )
    parser.add_argument(
        "--barbell-yield-futures-layer",
        action="store_true",
        help="Add CME/Yahoo yield futures as smaller rates expressions before ETF fallback.",
    )
    parser.add_argument(
        "--barbell-curve-rv-layer",
        action="store_true",
        help="Add a two-leg 5s10s yield-curve relative-value sleeve on top of the macro barbell.",
    )
    parser.add_argument(
        "--barbell-curve-rv-max-gross",
        type=float,
        default=0.20,
        help="Maximum gross allocation for the yield-curve relative-value sleeve.",
    )
    parser.add_argument(
        "--barbell-curve-rv-max-leg-weight",
        type=float,
        default=0.10,
        help="Maximum per-leg target weight for the yield-curve relative-value sleeve.",
    )
    parser.add_argument(
        "--barbell-curve-rv-regime-entry-score",
        type=float,
        default=1.50,
        help="Minimum US macro rate-cut/rate-hike regime score for curve RV trades.",
    )
    parser.add_argument(
        "--barbell-curve-rv-slope-z-entry",
        type=float,
        default=0.50,
        help="Minimum absolute 5s10s slope z-score for curve RV trades.",
    )
    parser.add_argument(
        "--barbell-cross-sleeve-amplifier-layer",
        action="store_true",
        help="Scale Engine B only when independent sleeves agree on the same macro direction.",
    )
    parser.add_argument(
        "--barbell-macro-surprise-layer",
        action="store_true",
        help="Use point-in-time macro surprise events as a score/coherence/phase accelerator.",
    )
    parser.add_argument(
        "--barbell-extreme-phase-skip-layer",
        action="store_true",
        help="Let rare extreme-conviction Engine B entries start at Phase 2 sizing/stops.",
    )
    parser.add_argument(
        "--barbell-rates-max-gross",
        type=float,
        default=0.45,
        help="Maximum gross allocation for the dedicated rates/curve sleeve.",
    )
    parser.add_argument(
        "--barbell-rates-max-symbol-weight",
        type=float,
        default=0.20,
        help="Maximum per-symbol target weight for the dedicated rates/curve sleeve.",
    )
    parser.add_argument(
        "--barbell-rates-duration-long-entry-score",
        type=float,
        default=1.25,
        help="Minimum score for recession/liquidity duration-long rates/curve signals.",
    )
    parser.add_argument(
        "--barbell-rates-duration-short-entry-score",
        type=float,
        default=0.95,
        help="Minimum score for inflation/hiking duration-short rates/curve signals.",
    )
    parser.add_argument(
        "--barbell-rates-duration-long-gross-mult",
        type=float,
        default=0.35,
        help="Gross-budget multiplier for recessionary duration-long rates/curve signals.",
    )
    parser.add_argument(
        "--barbell-rates-rolling-ev-gate-layer",
        action="store_true",
        help="Enable a point-in-time rolling forward-return EV gate for rates/curve candidates.",
    )
    parser.add_argument(
        "--barbell-rates-rolling-ev-min-obs",
        type=int,
        default=30,
        help="Minimum matured observations per rates EV bucket before the rolling EV gate can block.",
    )
    parser.add_argument(
        "--barbell-rates-rolling-ev-min-mean",
        type=float,
        default=0.0,
        help="Minimum average side-adjusted forward return required by the rates rolling EV gate.",
    )
    parser.add_argument(
        "--barbell-crisis-trend-layer",
        action="store_true",
        help="Enable a price-only crisis trend sleeve around Engine B.",
    )
    parser.add_argument(
        "--barbell-dollar-squeeze-layer",
        action="store_true",
        help="Enable a dedicated funding-stress / dollar-squeeze sleeve.",
    )
    parser.add_argument(
        "--barbell-liquidation-reversal-layer",
        action="store_true",
        help="Enable a post-liquidation reversal sleeve after confirmed washouts.",
    )
    parser.add_argument(
        "--barbell-radical-research-layer",
        action="store_true",
        help="Enable the research baseline plus crisis trend, dollar squeeze, and liquidation reversal sleeves.",
    )
    parser.add_argument(
        "--barbell-optimization-layer",
        action="store_true",
        help="Enable the proposed optimization bundle: expanded universe, dynamic Engine A, adaptive thresholds, Phase 4, vol targeting, and extra risk controls.",
    )
    parser.add_argument(
        "--barbell-expanded-universe-layer",
        action="store_true",
        help="Add TLT/GLD/TIP/IEF/EMB as Engine B candidates while preserving original thresholds and top-N.",
    )
    parser.add_argument(
        "--barbell-conditional-hedge-layer",
        action="store_true",
        help="Add TLT/IEF/GLD/TIP as small conditional hedge satellites without adding them to Engine B top-N.",
    )
    parser.add_argument(
        "--barbell-trend-sleeve-layer",
        action="store_true",
        help="Add a separate time-series momentum sleeve across liquid ETF/futures proxies.",
    )
    parser.add_argument(
        "--barbell-carry-crash-risk-layer",
        action="store_true",
        help="Scale Engine A carry down when credit/liquidity/price crash risk rises.",
    )
    parser.add_argument(
        "--barbell-safety-layer",
        action="store_true",
        help="Enable drawdown throttle and position-level NAV stops without changing alpha thresholds.",
    )
    parser.add_argument(
        "--barbell-revised-risk-layer",
        action="store_true",
        help="Enable the revised experiment: research baseline + liquidation reversal + expanded candidate universe + safety layer.",
    )
    return parser.parse_args()


def _shared_sources(
    macro_csv: str | Path | None = None,
    futures_curve_csv: str | Path | None = None,
    macro_surprise_csv: str | Path | None = None,
) -> dict[str, object]:
    resolved_macro = Path(macro_csv) if macro_csv else (DEFAULT_MACRO_PUBLIC if DEFAULT_MACRO_PUBLIC.exists() else DEFAULT_MACRO_LEGACY)
    resolved_futures_curve = Path(futures_curve_csv) if futures_curve_csv else DEFAULT_FUTURES_CURVE
    resolved_macro_surprise = (
        Path(macro_surprise_csv)
        if macro_surprise_csv
        else (DEFAULT_MACRO_SURPRISES if DEFAULT_MACRO_SURPRISES.exists() else None)
    )
    return {
        "security_master": CSVSecurityMasterDataSource(DATA_ROOT / "security_master.csv"),
        "fundamentals": CSVFundamentalsDataSource(DATA_ROOT / "fundamentals_pit.csv"),
        "catalysts": CSVCatalystDataSource(DATA_ROOT / "catalysts.csv"),
        "macro": CSVMacroDataSource(resolved_macro),
        "macro_path": resolved_macro,
        "futures_curve": (
            CSVFuturesCurveDataSource(resolved_futures_curve) if resolved_futures_curve.exists() else None
        ),
        "macro_surprises": (
            CSVMacroSurpriseDataSource(resolved_macro_surprise)
            if resolved_macro_surprise is not None and resolved_macro_surprise.exists()
            else None
        ),
        "volatility": CSVVolatilityDataSource(DATA_ROOT / "volatility_signals.csv"),
        "earnings": CSVEarningsDataSource(DATA_ROOT / "earnings_events.csv"),
    }


def _parse_symbol_csv(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _build_specs(
    sources: dict[str, object],
    *,
    enable_barbell_velocity_layer: bool = False,
    enable_barbell_phase_stop_layer: bool = False,
    enable_barbell_engine_a_reallocation_layer: bool = False,
    enable_barbell_asymmetric_phase_layer: bool = False,
    enable_barbell_extreme_concentration_layer: bool = False,
    enable_barbell_phase3_profit_rate_layer: bool = False,
    enable_barbell_overlay_routing_layer: bool = False,
    enable_barbell_focused_reallocation_layer: bool = False,
    enable_barbell_overlay_off: bool = False,
    enable_barbell_research_baseline: bool = False,
    enable_barbell_current_best: bool = False,
    enable_barbell_market_evidence_layer: bool = False,
    enable_barbell_robust_redesign: bool = False,
    disable_barbell_engine_b_core: bool = False,
    enable_barbell_cheap_discovery_layer: bool = False,
    enable_barbell_convex_dislocation_layer: bool = False,
    enable_barbell_commodity_futures_layer: bool = False,
    enable_barbell_commodity_futures_sleeve_layer: bool = False,
    barbell_commodity_futures_symbols_override: tuple[str, ...] | None = None,
    enable_barbell_futures_expression_layer: bool = False,
    enable_barbell_rates_curve_layer: bool = False,
    enable_barbell_yield_futures_layer: bool = False,
    barbell_rates_max_gross: float = 0.45,
    barbell_rates_max_symbol_weight: float = 0.20,
    barbell_rates_duration_long_entry_score: float = 1.25,
    barbell_rates_duration_short_entry_score: float = 0.95,
    barbell_rates_duration_long_gross_mult: float = 0.35,
    enable_barbell_rates_rolling_ev_gate_layer: bool = False,
    barbell_rates_rolling_ev_min_obs: int = 30,
    barbell_rates_rolling_ev_min_mean: float = 0.0,
    enable_barbell_curve_rv_layer: bool = False,
    barbell_curve_rv_max_gross: float = 0.20,
    barbell_curve_rv_max_leg_weight: float = 0.10,
    barbell_curve_rv_regime_entry_score: float = 1.50,
    barbell_curve_rv_slope_z_entry: float = 0.50,
    enable_barbell_cross_sleeve_amplifier_layer: bool = False,
    enable_barbell_macro_surprise_layer: bool = False,
    enable_barbell_extreme_phase_skip_layer: bool = False,
    enable_barbell_crisis_trend_layer: bool = False,
    enable_barbell_dollar_squeeze_layer: bool = False,
    enable_barbell_liquidation_reversal_layer: bool = False,
    enable_barbell_radical_research_layer: bool = False,
    enable_barbell_optimization_layer: bool = False,
    enable_barbell_expanded_universe_layer: bool = False,
    enable_barbell_conditional_hedge_layer: bool = False,
    enable_barbell_trend_sleeve_layer: bool = False,
    enable_barbell_carry_crash_risk_layer: bool = False,
    enable_barbell_safety_layer: bool = False,
    enable_barbell_revised_risk_layer: bool = False,
    macro_inputs_are_zscores: bool = False,
) -> dict[str, StrategyRunSpec]:
    security_master = sources["security_master"]
    fundamentals = sources["fundamentals"]
    catalysts = sources["catalysts"]
    macro = sources["macro"]
    volatility = sources["volatility"]
    earnings = sources["earnings"]
    if enable_barbell_robust_redesign:
        enable_barbell_current_best = True
        enable_barbell_market_evidence_layer = True
        enable_barbell_trend_sleeve_layer = True
        enable_barbell_cheap_discovery_layer = True
    if enable_barbell_current_best:
        enable_barbell_research_baseline = True
        enable_barbell_liquidation_reversal_layer = True
        enable_barbell_carry_crash_risk_layer = True
        enable_barbell_rates_curve_layer = True
        enable_barbell_yield_futures_layer = True
        enable_barbell_commodity_futures_sleeve_layer = True
    if enable_barbell_research_baseline:
        enable_barbell_extreme_concentration_layer = True
        enable_barbell_phase3_profit_rate_layer = True
        enable_barbell_overlay_off = True
    if enable_barbell_radical_research_layer:
        enable_barbell_research_baseline = True
        enable_barbell_extreme_concentration_layer = True
        enable_barbell_phase3_profit_rate_layer = True
        enable_barbell_overlay_off = True
        enable_barbell_crisis_trend_layer = True
        enable_barbell_dollar_squeeze_layer = True
        enable_barbell_liquidation_reversal_layer = True
    if enable_barbell_optimization_layer:
        enable_barbell_research_baseline = True
        enable_barbell_extreme_concentration_layer = True
        enable_barbell_phase3_profit_rate_layer = True
        enable_barbell_overlay_off = True
        enable_barbell_liquidation_reversal_layer = True
    if enable_barbell_revised_risk_layer:
        enable_barbell_research_baseline = True
        enable_barbell_extreme_concentration_layer = True
        enable_barbell_phase3_profit_rate_layer = True
        enable_barbell_overlay_off = True
        enable_barbell_liquidation_reversal_layer = True
        enable_barbell_expanded_universe_layer = True
        enable_barbell_safety_layer = True
    effective_extreme_concentration = (
        enable_barbell_extreme_concentration_layer or enable_barbell_focused_reallocation_layer
    )
    barbell_reallocation_enabled = (
        enable_barbell_engine_a_reallocation_layer
        or enable_barbell_asymmetric_phase_layer
        or effective_extreme_concentration
    )
    barbell_phase_enabled = (
        enable_barbell_phase_stop_layer
        or barbell_reallocation_enabled
        or enable_barbell_convex_dislocation_layer
    )
    barbell_velocity_enabled = enable_barbell_velocity_layer or barbell_phase_enabled
    if enable_barbell_robust_redesign and disable_barbell_engine_b_core:
        barbell_name = "global_macro_barbell_robustredesign_nocore"
    elif enable_barbell_robust_redesign:
        barbell_name = "global_macro_barbell_robustredesign"
    elif enable_barbell_current_best:
        barbell_name = "global_macro_barbell_currentbest"
    elif enable_barbell_optimization_layer:
        barbell_name = "global_macro_barbell_optimized"
    elif enable_barbell_revised_risk_layer:
        barbell_name = "global_macro_barbell_revisedrisk"
    elif enable_barbell_radical_research_layer:
        barbell_name = "global_macro_barbell_radical"
    elif enable_barbell_research_baseline:
        barbell_name = "global_macro_barbell_research"
    elif enable_barbell_convex_dislocation_layer:
        barbell_name = "global_macro_barbell_convexproxy"
    elif enable_barbell_commodity_futures_layer:
        barbell_name = "global_macro_barbell_futuresproxy"
    elif enable_barbell_focused_reallocation_layer:
        barbell_name = "global_macro_barbell_focusedrealloc"
    elif enable_barbell_overlay_off:
        barbell_name = "global_macro_barbell_overlayoff"
    elif enable_barbell_overlay_routing_layer:
        barbell_name = "global_macro_barbell_overlayroute"
    elif enable_barbell_phase3_profit_rate_layer:
        barbell_name = "global_macro_barbell_phase3rate"
    elif effective_extreme_concentration:
        barbell_name = "global_macro_barbell_concentration"
    elif enable_barbell_asymmetric_phase_layer:
        barbell_name = "global_macro_barbell_asymmetric"
    elif barbell_reallocation_enabled:
        barbell_name = "global_macro_barbell_reallocation"
    elif enable_barbell_phase_stop_layer:
        barbell_name = "global_macro_barbell_phase"
    elif enable_barbell_velocity_layer:
        barbell_name = "global_macro_barbell_velocity"
    else:
        barbell_name = "global_macro_barbell"
    if enable_barbell_curve_rv_layer:
        barbell_name = f"{barbell_name}_curverv"
    if enable_barbell_cross_sleeve_amplifier_layer:
        barbell_name = f"{barbell_name}_xsleeveamp"
    if enable_barbell_macro_surprise_layer:
        barbell_name = f"{barbell_name}_macrosurprise"
    if enable_barbell_extreme_phase_skip_layer:
        barbell_name = f"{barbell_name}_phaseskip"
    if enable_barbell_futures_expression_layer:
        enable_barbell_commodity_futures_sleeve_layer = True
        enable_barbell_rates_curve_layer = True
        enable_barbell_yield_futures_layer = True
    if enable_barbell_commodity_futures_layer and enable_barbell_research_baseline:
        barbell_name = f"{barbell_name}_futuresproxy"
    if enable_barbell_futures_expression_layer and enable_barbell_research_baseline:
        barbell_name = f"{barbell_name}_futuresexpr"
    if enable_barbell_convex_dislocation_layer and enable_barbell_research_baseline:
        barbell_name = f"{barbell_name}_convexproxy"
    if (
        enable_barbell_research_baseline
        and not enable_barbell_radical_research_layer
        and not enable_barbell_optimization_layer
        and not enable_barbell_revised_risk_layer
        and not enable_barbell_current_best
    ):
        layer_suffixes: list[str] = []
        if enable_barbell_crisis_trend_layer:
            layer_suffixes.append("crisistrend")
        if enable_barbell_dollar_squeeze_layer:
            layer_suffixes.append("dollarsqueeze")
        if enable_barbell_liquidation_reversal_layer:
            layer_suffixes.append("reversal")
        if enable_barbell_expanded_universe_layer:
            layer_suffixes.append("expanded")
        if enable_barbell_conditional_hedge_layer:
            layer_suffixes.append("hedge")
        if enable_barbell_trend_sleeve_layer:
            layer_suffixes.append("trend")
        if enable_barbell_carry_crash_risk_layer:
            layer_suffixes.append("carryrisk")
        if enable_barbell_rates_curve_layer:
            layer_suffixes.append("rates")
        if enable_barbell_yield_futures_layer:
            layer_suffixes.append("yieldfutures")
        if enable_barbell_rates_rolling_ev_gate_layer:
            layer_suffixes.append("ratesevgate")
        if enable_barbell_commodity_futures_sleeve_layer:
            layer_suffixes.append("commodityfutures")
        if enable_barbell_safety_layer:
            layer_suffixes.append("safety")
        if layer_suffixes:
            barbell_name = f"{barbell_name}_{'_'.join(layer_suffixes)}"

    if enable_barbell_optimization_layer or enable_barbell_expanded_universe_layer:
        barbell_dislocation_symbols = ["SPY", "EEM", "DBC", "USO", "UUP", "TLT", "GLD", "TIP", "EMB", "IEF"]
    else:
        barbell_dislocation_symbols = (
            ["SPY", "EEM", "CL=F", "GC=F", "HG=F", "UUP"]
            if enable_barbell_commodity_futures_layer
            else ["SPY", "EEM", "DBC", "USO", "UUP"]
        )
    barbell_hedge_symbols = ["TLT", "IEF", "GLD", "TIP"] if enable_barbell_conditional_hedge_layer else []
    if enable_barbell_trend_sleeve_layer:
        barbell_trend_symbols = (
            ["SPY", "EEM", "ZN=F", "ZB=F", "CL=F", "GC=F", "HG=F", "UUP"]
            if enable_barbell_commodity_futures_layer
            else ["SPY", "EEM", "TLT", "IEF", "GLD", "DBC", "USO", "UUP"]
        )
    else:
        barbell_trend_symbols = []
    barbell_rates_symbols = []
    if enable_barbell_rates_curve_layer or enable_barbell_curve_rv_layer:
        if enable_barbell_yield_futures_layer:
            barbell_rates_symbols.extend(["2YY=F", "^FVX", "^TNX", "^TYX"])
        if enable_barbell_curve_rv_layer:
            barbell_rates_symbols.extend(["ZF=F", "ZN=F", "^FVX", "^TNX"])
        barbell_rates_symbols.extend(["ZT=F", "ZN=F", "ZB=F"])
    barbell_rates_proxy_symbols = ["IEF", "TLT"] if enable_barbell_rates_curve_layer else []
    barbell_commodity_futures_symbols = (
        list(barbell_commodity_futures_symbols_override or ("HG=F",))
        if enable_barbell_commodity_futures_sleeve_layer
        else []
    )
    barbell_symbols = list(
        dict.fromkeys(
            [
                "HYG",
                "LQD",
                *barbell_dislocation_symbols,
                *barbell_hedge_symbols,
                *barbell_trend_symbols,
                *barbell_rates_symbols,
                *barbell_rates_proxy_symbols,
                *barbell_commodity_futures_symbols,
                "EMB",
            ]
        )
    )

    specs = {
        "trend_breakout": StrategyRunSpec(
            name="trend_breakout",
            symbols=["SPY", "QQQ", "TLT", "GLD", "XLE", "XLK", "AAPL", "MSFT", "NVDA", "BHP"],
            strategy=TrendBreakoutWizardStrategy(
                symbols=["SPY", "QQQ", "TLT", "GLD", "XLE", "XLK", "AAPL", "MSFT", "NVDA", "BHP"]
            ),
            leverage=2.0,
            max_gross=2.0,
            max_net=1.0,
            hard_stop=0.20,
            precise=True,
            note="Price/ATR breakout logic is supported directly by the engine.",
        ),
        "trend_pullback": StrategyRunSpec(
            name="trend_pullback",
            symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "META", "TSM", "ASML"],
            strategy=TrendPullbackReversalWizardStrategy(
                symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "META", "TSM", "ASML"]
            ),
            leverage=2.0,
            max_gross=2.0,
            max_net=1.0,
            hard_stop=0.18,
            precise=True,
            note="Linear pullback/reversal entries are supported directly.",
        ),
        "leader_breakout": StrategyRunSpec(
            name="leader_breakout",
            symbols=["AAPL", "MSFT", "NVDA", "AMD", "META", "SHOP.TO", "TSM", "ASML", "SAP"],
            strategy=StockLeadershipBreakoutWizardStrategy(
                symbols=["AAPL", "MSFT", "NVDA", "AMD", "META", "SHOP.TO", "TSM", "ASML", "SAP"],
                fundamentals_source=fundamentals,  # type: ignore[arg-type]
                security_master=security_master,  # type: ignore[arg-type]
            ),
            leverage=1.5,
            max_gross=1.5,
            max_net=1.0,
            hard_stop=0.12,
            precise=True,
            note="Leader logic is supported once PiT industry and growth fields are supplied.",
        ),
        "event_momentum": StrategyRunSpec(
            name="event_momentum",
            symbols=["AAPL", "MSFT", "NVDA", "TSLA", "META", "NFLX", "AMD", "SPY"],
            strategy=EventMomentumWizardStrategy(
                symbols=["AAPL", "MSFT", "NVDA", "TSLA", "META", "NFLX", "AMD"],
                earnings_source=earnings,  # type: ignore[arg-type]
                benchmark_symbol="SPY",
                surprise_threshold=1.0,
            ),
            leverage=2.0,
            max_gross=2.0,
            max_net=1.0,
            hard_stop=0.18,
            precise=True,
            note="Next-open event execution is supported directly with PiT earnings events.",
        ),
        "value_catalyst": StrategyRunSpec(
            name="value_catalyst",
            symbols=["BABA", "SHEL", "RIO", "BHP", "SAP", "NFLX", "TSLA", "KO", "PG"],
            strategy=ValueCatalystWizardStrategy(
                symbols=["BABA", "SHEL", "RIO", "BHP", "SAP", "NFLX", "TSLA", "KO", "PG"],
                fundamentals_source=fundamentals,  # type: ignore[arg-type]
                catalyst_source=catalysts,  # type: ignore[arg-type]
            ),
            leverage=2.0,
            max_gross=2.0,
            max_net=1.0,
            hard_stop=0.15,
            precise=True,
            note="Valuation plus catalyst is supported once PiT catalyst intervals are supplied.",
        ),
        "global_macro": StrategyRunSpec(
            name="global_macro",
            symbols=["SPY", "EFA", "EEM", "EWJ", "TLT", "IEF", "TIP", "GLD", "USO", "DBC", "UUP", "HYG", "LQD"],
            strategy=TopDownGlobalMacroWizardStrategy(
                symbols=["SPY", "EFA", "EEM", "EWJ", "TLT", "IEF", "TIP", "GLD", "USO", "DBC", "UUP", "HYG", "LQD"],
                macro_source=macro,  # type: ignore[arg-type]
                security_master=security_master,  # type: ignore[arg-type]
                macro_inputs_are_zscores=macro_inputs_are_zscores,
            ),
            leverage=2.5,
            max_gross=2.5,
            max_net=1.2,
            hard_stop=0.20,
            precise=True,
            note="Cross-asset macro ETF proxies are supported with PiT regime snapshots and linear instruments.",
        ),
        "global_macro_barbell": StrategyRunSpec(
            name=barbell_name,
            symbols=barbell_symbols,
            strategy=BarbellMacroWizardStrategy(
                symbols=barbell_symbols,
                macro_source=macro,  # type: ignore[arg-type]
                security_master=security_master,  # type: ignore[arg-type]
                futures_curve_source=sources.get("futures_curve"),  # type: ignore[arg-type]
                macro_surprise_source=sources.get("macro_surprises"),  # type: ignore[arg-type]
                dislocation_symbols=tuple(barbell_dislocation_symbols),
                enable_velocity_layer=barbell_velocity_enabled,
                enable_phase_stop_layer=barbell_phase_enabled,
                enable_engine_a_reallocation_layer=barbell_reallocation_enabled,
                enable_asymmetric_phase_timing_layer=enable_barbell_asymmetric_phase_layer,
                enable_extreme_concentration_layer=effective_extreme_concentration,
                enable_phase3_profit_rate_layer=enable_barbell_phase3_profit_rate_layer,
                enable_focused_reallocation_layer=enable_barbell_focused_reallocation_layer,
                enable_overlay_routing_layer=enable_barbell_overlay_routing_layer,
                overlay_off=enable_barbell_overlay_off,
                enable_conditional_hedge_layer=enable_barbell_conditional_hedge_layer,
                enable_time_series_momentum_layer=enable_barbell_trend_sleeve_layer,
                trend_sleeve_symbols=tuple(barbell_trend_symbols) if barbell_trend_symbols else ("SPY", "EEM", "TLT", "IEF", "GLD", "DBC", "USO", "UUP"),
                enable_rates_curve_layer=enable_barbell_rates_curve_layer,
                rates_curve_symbols=tuple(barbell_rates_symbols),
                rates_proxy_symbols=tuple(barbell_rates_proxy_symbols),
                rates_curve_max_gross=barbell_rates_max_gross,
                rates_curve_max_symbol_weight=barbell_rates_max_symbol_weight,
                rates_curve_duration_long_entry_score=barbell_rates_duration_long_entry_score,
                rates_curve_duration_short_entry_score=barbell_rates_duration_short_entry_score,
                rates_curve_duration_long_gross_mult=barbell_rates_duration_long_gross_mult,
                enable_rates_rolling_ev_gate_layer=enable_barbell_rates_rolling_ev_gate_layer,
                rates_rolling_ev_min_obs=barbell_rates_rolling_ev_min_obs,
                rates_rolling_ev_min_mean=barbell_rates_rolling_ev_min_mean,
                enable_curve_rv_layer=enable_barbell_curve_rv_layer,
                curve_rv_max_gross=barbell_curve_rv_max_gross,
                curve_rv_max_leg_weight=barbell_curve_rv_max_leg_weight,
                curve_rv_regime_entry_score=barbell_curve_rv_regime_entry_score,
                curve_rv_slope_z_entry=barbell_curve_rv_slope_z_entry,
                enable_commodity_futures_sleeve_layer=enable_barbell_commodity_futures_sleeve_layer,
                commodity_futures_symbols=tuple(barbell_commodity_futures_symbols),
                instrument_multipliers={
                    symbol: multiplier
                    for symbol, multiplier in FUTURES_MULTIPLIERS.items()
                    if symbol in barbell_symbols
                },
                enable_convex_dislocation_layer=enable_barbell_convex_dislocation_layer,
                enable_cross_sleeve_amplifier_layer=enable_barbell_cross_sleeve_amplifier_layer,
                enable_macro_surprise_layer=enable_barbell_macro_surprise_layer,
                enable_market_evidence_layer=enable_barbell_market_evidence_layer,
                enable_engine_b_core=not disable_barbell_engine_b_core,
                enable_cheap_discovery_layer=enable_barbell_cheap_discovery_layer,
                enable_extreme_phase_skip_layer=enable_barbell_extreme_phase_skip_layer,
                enable_crisis_trend_layer=enable_barbell_crisis_trend_layer,
                enable_dollar_squeeze_layer=enable_barbell_dollar_squeeze_layer,
                enable_liquidation_reversal_layer=enable_barbell_liquidation_reversal_layer,
                enable_optimization_layer=enable_barbell_optimization_layer,
                enable_carry_crash_risk_layer=enable_barbell_carry_crash_risk_layer,
                enable_drawdown_throttle_layer=enable_barbell_safety_layer,
                enable_nav_stop_layer=enable_barbell_safety_layer,
                engine_b_probe_threshold=0.70 if enable_barbell_optimization_layer else 0.80,
                engine_b_conviction_threshold=1.05 if enable_barbell_optimization_layer else 1.20,
                engine_b_max_threshold=1.65 if enable_barbell_optimization_layer else 1.80,
                engine_b_max_gross=2.30 if enable_barbell_optimization_layer else 2.15,
                engine_b_top_n=4 if enable_barbell_optimization_layer else 3,
                max_gross_total=3.0 if enable_barbell_optimization_layer else 2.5,
                max_net_total=1.4 if enable_barbell_optimization_layer else 1.2,
                max_gross_per_asset_class=0.85 if enable_barbell_optimization_layer else 0.70,
                max_gross_per_region=0.70 if enable_barbell_optimization_layer else 0.60,
                macro_inputs_are_zscores=macro_inputs_are_zscores,
            ),
            leverage=3.0 if enable_barbell_optimization_layer else 2.5,
            max_gross=3.0 if enable_barbell_optimization_layer else 2.5,
            max_net=1.4 if enable_barbell_optimization_layer else 1.2,
            hard_stop=0.25 if enable_barbell_optimization_layer else 0.20,
            precise=True,
            note=(
                "Barbell split: always-on credit carry sleeve plus concentrated macro dislocation sleeve. "
                "Convex and commodity-futures modes are linear proxy implementations, not full options/futures accounting."
            ),
            optional_symbols=[
                "EMB",
                *(
                    sorted(RATE_YIELD_FUTURES | RATE_YIELD_INDEX_PROXIES)
                    if (enable_barbell_yield_futures_layer or enable_barbell_curve_rv_layer)
                    else []
                ),
            ],
        ),
        "relative_value": StrategyRunSpec(
            name="relative_value",
            symbols=["JPM", "BAC", "XOM", "SHEL", "KO", "PG", "SPY", "QQQ"],
            strategy=RelativeValueStatArbWizardStrategy(
                pairs=[
                    PairSpec("JPM", "BAC"),
                    PairSpec("XOM", "SHEL"),
                    PairSpec("KO", "PG"),
                    PairSpec("SPY", "QQQ"),
                ]
            ),
            leverage=3.0,
            max_gross=2.5,
            max_net=0.5,
            hard_stop=0.15,
            precise=True,
            note="Linear pair spreads and beta hedges are supported directly.",
        ),
        "convexity": StrategyRunSpec(
            name="convexity",
            symbols=["TSLA", "NVDA", "NFLX", "AMD", "QQQ"],
            strategy=LongVolatilityConvexityWizardStrategy(
                symbols=["TSLA", "NVDA", "NFLX", "AMD", "QQQ"],
                volatility_source=volatility,  # type: ignore[arg-type]
            ),
            leverage=1.5,
            max_gross=1.0,
            max_net=1.0,
            hard_stop=0.12,
            precise=False,
            note="This is a convexity proxy. The current engine does not price nonlinear option/CDS payoffs exactly.",
        ),
    }
    return specs


def _run_spec(
    *,
    spec: StrategyRunSpec,
    start: datetime,
    end: datetime,
    interval: str,
    initial_cash: float,
    output_dir: Path,
    no_report: bool,
) -> BacktestResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_handler = PublicOHLCVDataHandler(
        config=DataHandlerConfig(
            symbols=spec.symbols,
            start=start,
            end=end,
            interval=interval,
            adjust_prices=True,
            required_symbols=set(spec.symbols).difference(spec.optional_symbols),
        )
    )

    portfolio = Portfolio(
        initial_cash=initial_cash,
        leverage=spec.leverage,
        maintenance_margin_ratio=0.25,
        position_sizer=FixedFractionalSizer(risk_fraction=0.01),
        max_gross_exposure=spec.max_gross,
        max_net_exposure=spec.max_net,
        symbol_multipliers={
            symbol: multiplier
            for symbol, multiplier in FUTURES_MULTIPLIERS.items()
            if symbol in spec.symbols
        },
        futures_symbols={symbol for symbol in spec.symbols if symbol in FUTURES_SYMBOLS},
        hard_stop_loss_pct=spec.hard_stop,
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
        symbol_multipliers={
            symbol: multiplier
            for symbol, multiplier in FUTURES_MULTIPLIERS.items()
            if symbol in spec.symbols
        },
        futures_symbols={symbol for symbol in spec.symbols if symbol in FUTURES_SYMBOLS},
    )
    reporter = None if no_report else TearSheetReporter(output_dir=output_dir)
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=spec.strategy,  # type: ignore[arg-type]
        portfolio=portfolio,
        execution_handler=execution,
        reporter=reporter,
    )
    result = engine.run(run_name=spec.name)
    if "metrics_json" not in result.artifacts:
        metrics_path = output_dir / f"{spec.name}_metrics.json"
        with metrics_path.open("w", encoding="utf-8") as handle:
            json.dump(result.report.metrics, handle, indent=2)
        result.artifacts["metrics_json"] = str(metrics_path)
    if hasattr(spec.strategy, "diagnostics"):
        diagnostics = spec.strategy.diagnostics()  # type: ignore[assignment]
        if diagnostics:
            diagnostics_path = output_dir / f"{spec.name}_diagnostics.json"
            with diagnostics_path.open("w", encoding="utf-8") as handle:
                json.dump(diagnostics, handle, indent=2)
            result.artifacts["diagnostics_json"] = str(diagnostics_path)
    if hasattr(spec.strategy, "candidate_research_frame"):
        candidate_frame = spec.strategy.candidate_research_frame()  # type: ignore[assignment]
        if candidate_frame is not None and not candidate_frame.empty:
            candidate_path = output_dir / f"{spec.name}_candidate_research.csv"
            candidate_frame.to_csv(candidate_path, index=False)
            result.artifacts["candidate_research_csv"] = str(candidate_path)
    if hasattr(spec.strategy, "trade_attribution"):
        attribution = spec.strategy.trade_attribution()  # type: ignore[assignment]
        if attribution:
            attribution_path = output_dir / f"{spec.name}_attribution.json"
            with attribution_path.open("w", encoding="utf-8") as handle:
                json.dump(attribution, handle, indent=2)
            result.artifacts["attribution_json"] = str(attribution_path)
    return result


def _print_support(specs: dict[str, StrategyRunSpec]) -> None:
    print("=== Support Check ===")
    for key in (
        "trend_breakout",
        "trend_pullback",
        "leader_breakout",
        "event_momentum",
        "value_catalyst",
        "global_macro",
        "global_macro_barbell",
        "relative_value",
        "convexity",
    ):
        spec = specs[key]
        status = "PRECISE" if spec.precise else "PROXY"
        print(f"{key:>18}: {status} | {spec.note}")
    print()


def _print_result(spec: StrategyRunSpec, result: BacktestResult) -> None:
    print(f"=== {spec.name} ===")
    print(f"support                 : {'precise' if spec.precise else 'proxy'}")
    for key in (
        "annualized_return",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "calmar_ratio",
        "win_rate",
        "profit_factor",
        "num_closed_trades",
    ):
        value = float(result.report.metrics.get(key, 0.0))
        print(f"{key:>24}: {value:.6f}")
    print(f"{'submitted_orders':>24}: {result.submitted_orders}")
    print(f"{'fills':>24}: {result.fills}")
    print(f"{'risk_rejections':>24}: {result.risk_rejections}")
    print(f"{'margin_calls':>24}: {result.margin_calls}")
    print(f"{'metrics_json':>24}: {result.artifacts.get('metrics_json', '')}")
    if "diagnostics_json" in result.artifacts:
        print(f"{'diagnostics_json':>24}: {result.artifacts.get('diagnostics_json', '')}")
    if "candidate_research_csv" in result.artifacts:
        print(f"{'candidate_research_csv':>24}: {result.artifacts.get('candidate_research_csv', '')}")
    if "attribution_json" in result.artifacts:
        print(f"{'attribution_json':>24}: {result.artifacts.get('attribution_json', '')}")
    print()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    output_dir = Path(args.output_dir)
    sources = _shared_sources(args.macro_csv, args.futures_curve_csv, args.macro_surprise_csv)
    if args.barbell_macro_surprise_layer and sources.get("macro_surprises") is None:
        raise SystemExit(
            "Missing macro surprise dataset. Build one with "
            "`python examples/build_macro_surprises_from_events.py` after creating "
            "`data/market_wizards/macro_factor_events_public.csv`, or pass "
            "`--macro-surprise-csv path/to/macro_surprises.csv`."
        )
    macro_path = Path(sources.get("macro_path", ""))
    macro_inputs_are_zscores = bool(
        args.macro_inputs_are_zscores
        or macro_path.name == DEFAULT_MACRO_PUBLIC.name
    )
    specs = _build_specs(
        sources,
        enable_barbell_velocity_layer=args.barbell_velocity_layer,
        enable_barbell_phase_stop_layer=args.barbell_phase_stop_layer,
        enable_barbell_engine_a_reallocation_layer=args.barbell_engine_a_reallocation_layer,
        enable_barbell_asymmetric_phase_layer=args.barbell_asymmetric_phase_layer,
        enable_barbell_extreme_concentration_layer=args.barbell_extreme_concentration_layer,
        enable_barbell_phase3_profit_rate_layer=args.barbell_phase3_profit_rate_layer,
        enable_barbell_overlay_routing_layer=args.barbell_overlay_routing_layer,
        enable_barbell_focused_reallocation_layer=args.barbell_focused_reallocation_layer,
        enable_barbell_overlay_off=args.barbell_overlay_off,
        enable_barbell_research_baseline=args.barbell_research_baseline,
        enable_barbell_current_best=args.barbell_current_best,
        enable_barbell_market_evidence_layer=args.barbell_market_evidence_layer,
        enable_barbell_robust_redesign=args.barbell_robust_redesign,
        disable_barbell_engine_b_core=args.barbell_disable_engine_b_core,
        enable_barbell_cheap_discovery_layer=args.barbell_cheap_discovery_layer,
        enable_barbell_convex_dislocation_layer=args.barbell_convex_dislocation_layer,
        enable_barbell_commodity_futures_layer=args.barbell_commodity_futures_layer,
        enable_barbell_commodity_futures_sleeve_layer=args.barbell_commodity_futures_sleeve_layer,
        barbell_commodity_futures_symbols_override=tuple(_parse_symbol_csv(args.barbell_commodity_futures_symbols)),
        enable_barbell_futures_expression_layer=args.barbell_futures_expression_layer,
        enable_barbell_rates_curve_layer=args.barbell_rates_curve_layer,
        enable_barbell_yield_futures_layer=args.barbell_yield_futures_layer,
        barbell_rates_max_gross=args.barbell_rates_max_gross,
        barbell_rates_max_symbol_weight=args.barbell_rates_max_symbol_weight,
        barbell_rates_duration_long_entry_score=args.barbell_rates_duration_long_entry_score,
        barbell_rates_duration_short_entry_score=args.barbell_rates_duration_short_entry_score,
        barbell_rates_duration_long_gross_mult=args.barbell_rates_duration_long_gross_mult,
        enable_barbell_rates_rolling_ev_gate_layer=args.barbell_rates_rolling_ev_gate_layer,
        barbell_rates_rolling_ev_min_obs=args.barbell_rates_rolling_ev_min_obs,
        barbell_rates_rolling_ev_min_mean=args.barbell_rates_rolling_ev_min_mean,
        enable_barbell_curve_rv_layer=args.barbell_curve_rv_layer,
        barbell_curve_rv_max_gross=args.barbell_curve_rv_max_gross,
        barbell_curve_rv_max_leg_weight=args.barbell_curve_rv_max_leg_weight,
        barbell_curve_rv_regime_entry_score=args.barbell_curve_rv_regime_entry_score,
        barbell_curve_rv_slope_z_entry=args.barbell_curve_rv_slope_z_entry,
        enable_barbell_cross_sleeve_amplifier_layer=args.barbell_cross_sleeve_amplifier_layer,
        enable_barbell_macro_surprise_layer=args.barbell_macro_surprise_layer,
        enable_barbell_extreme_phase_skip_layer=args.barbell_extreme_phase_skip_layer,
        enable_barbell_crisis_trend_layer=args.barbell_crisis_trend_layer,
        enable_barbell_dollar_squeeze_layer=args.barbell_dollar_squeeze_layer,
        enable_barbell_liquidation_reversal_layer=args.barbell_liquidation_reversal_layer,
        enable_barbell_radical_research_layer=args.barbell_radical_research_layer,
        enable_barbell_optimization_layer=args.barbell_optimization_layer,
        enable_barbell_expanded_universe_layer=args.barbell_expanded_universe_layer,
        enable_barbell_conditional_hedge_layer=args.barbell_conditional_hedge_layer,
        enable_barbell_trend_sleeve_layer=args.barbell_trend_sleeve_layer,
        enable_barbell_carry_crash_risk_layer=args.barbell_carry_crash_risk_layer,
        enable_barbell_safety_layer=args.barbell_safety_layer,
        enable_barbell_revised_risk_layer=args.barbell_revised_risk_layer,
        macro_inputs_are_zscores=macro_inputs_are_zscores,
    )
    _print_support(specs)

    selected = list(specs.keys()) if args.strategy == "all" else [args.strategy]
    for name in selected:
        spec = specs[name]
        result = _run_spec(
            spec=spec,
            start=start,
            end=end,
            interval=args.interval,
            initial_cash=args.initial_cash,
            output_dir=output_dir,
            no_report=args.no_report,
        )
        _print_result(spec, result)


if __name__ == "__main__":
    main()
