from __future__ import annotations

from collections import deque
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from quantbt.altdata.base import MacroDataSource
from quantbt.altdata.csv_sources import (
    CSVFuturesCurveDataSource,
    CSVMacroDataSource,
    CSVMacroSurpriseDataSource,
    CSVSecurityMasterDataSource,
)
from quantbt.altdata.futures_curve_builder import build_futures_curve_signal_frame
from quantbt.altdata.eia_futures_curve import build_eia_wti_futures_curve_frame
from quantbt.altdata.macro_builder import (
    PublicMacroBuilderConfig,
    build_macro_surprise_event_frame,
    build_public_macro_snapshot_frame,
)
from quantbt.altdata.macro_public_ingest import (
    build_public_macro_events_from_config,
    fetch_public_macro_series,
    load_public_macro_series_specs,
    normalize_public_macro_series_frame,
)
from quantbt.altdata.models import MacroSnapshot
from quantbt.core.enums import Side
from quantbt.core.events import FillEvent, MarketEvent
from quantbt.core.models import Bar, ClosedTrade
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.market_wizards import BarbellTargetPlan
from quantbt.strategies import BarbellMacroWizardStrategy, TopDownGlobalMacroWizardStrategy


class DummyMacroSource(MacroDataSource):
    def __init__(self, rows: dict[str, list[MacroSnapshot]]) -> None:
        self.rows = rows

    def snapshot(self, region: str, as_of: date) -> MacroSnapshot | None:
        history = self.history(region, as_of)
        return history[-1] if history else None

    def history(self, region: str, as_of: date, lookback: int | None = None) -> list[MacroSnapshot]:
        region = region.upper()
        rows = [row for row in self.rows.get(region, []) if row.tradable_from <= as_of]
        if lookback is not None:
            rows = rows[-lookback:]
        return rows

    def regions(self) -> list[str]:
        return sorted(self.rows)


def _write_security_master(path: Path) -> CSVSecurityMasterDataSource:
    path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    return CSVSecurityMasterDataSource(path)


def _write_credit_security_master(path: Path) -> CSVSecurityMasterDataSource:
    path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
            ]
        ),
        encoding="ascii",
    )
    return CSVSecurityMasterDataSource(path)


def test_macro_csv_source_filters_by_tradable_from_and_keeps_first_release(tmp_path):
    csv_path = tmp_path / "macro.csv"
    csv_path.write_text(
        "\n".join(
            [
                "date,region,release_date,tradable_from,growth_surprise,inflation_surprise,policy_surprise,liquidity_surprise,credit_surprise",
                "2024-01-01,US,2024-01-15,2024-01-22,0.5,0.1,0.0,0.2,0.1",
                "2024-01-01,US,2024-02-01,2024-02-08,9.9,9.9,9.9,9.9,9.9",
                "2024-02-01,US,2024-02-15,2024-02-22,-0.2,0.0,0.1,0.3,-0.1",
            ]
        ),
        encoding="ascii",
    )

    source = CSVMacroDataSource(csv_path)
    assert source.snapshot("US", date(2024, 1, 21)) is None

    first = source.snapshot("US", date(2024, 1, 23))
    assert first is not None
    assert first.growth_surprise == 0.5
    assert first.release_date == date(2024, 1, 15)

    history = source.history("US", date(2024, 2, 25))
    assert len(history) == 2
    assert source.regions() == ["US"]


def test_futures_curve_builder_creates_point_in_time_carry_signals(tmp_path):
    contracts_csv = tmp_path / "futures_contracts.csv"
    contracts_csv.write_text(
        "\n".join(
            [
                "symbol,date,contract_rank,contract,close",
                "CL=F,2024-01-31,1,CLH24,80.00",
                "CL=F,2024-01-31,2,CLJ24,78.00",
                "CL=F,2024-01-31,3,CLK24,77.00",
            ]
        ),
        encoding="ascii",
    )

    frame = build_futures_curve_signal_frame(
        contracts_csv,
        publication_lag_business_days=2,
        source_label="TEST",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["symbol"] == "CL=F"
    assert row["tradable_from"] == "2024-02-02"
    assert float(row["roll_yield_1m"]) > 0.0
    assert float(row["carry_score"]) > 0.0
    assert row["front_contract"] == "CLH24"


def test_eia_wti_curve_uses_fourth_contract_and_handles_negative_front_price():
    dates = pd.to_datetime(["2020-04-17", "2020-04-20"])
    frames = {
        1: pd.DataFrame({"date": dates, "contract_1_price": [18.27, -37.63]}),
        2: pd.DataFrame({"date": dates, "contract_2_price": [25.03, 20.43]}),
        3: pd.DataFrame({"date": dates, "contract_3_price": [29.11, 26.28]}),
        4: pd.DataFrame({"date": dates, "contract_4_price": [31.25, 29.77]}),
    }

    curve = build_eia_wti_futures_curve_frame(frames, publication_lag_business_days=1)

    assert len(curve) == 2
    assert curve.iloc[0]["fourth_contract"] == "RCLC4"
    assert curve.iloc[0]["tradable_from"] == "2020-04-20"
    assert float(curve.iloc[1]["carry_score"]) == pytest.approx(-1.0)
    assert float(curve.iloc[1]["front_price"]) == pytest.approx(-37.63)


def test_futures_curve_csv_source_filters_by_tradable_from(tmp_path):
    curve_csv = tmp_path / "futures_curve_signals.csv"
    curve_csv.write_text(
        "\n".join(
            [
                "symbol,date,release_date,tradable_from,front_contract,second_contract,third_contract,front_price,second_price,third_price,roll_yield_1m,roll_yield_3m,carry_score,backwardation_score,liquidity_score,source_label",
                "CL=F,2024-01-31,2024-01-31,2024-02-02,CLH24,CLJ24,CLK24,80,78,77,0.3077,0.1558,0.2621,0.0256,1.0,TEST",
            ]
        ),
        encoding="ascii",
    )

    source = CSVFuturesCurveDataSource(curve_csv)
    assert source.snapshot("CL=F", date(2024, 2, 1)) is None
    snapshot = source.snapshot("CL=F", date(2024, 2, 2))
    assert snapshot is not None
    assert snapshot.front_contract == "CLH24"
    assert snapshot.carry_score == pytest.approx(0.2621)


def test_barbell_rates_curve_short_future_uses_negative_contracts_when_sufficient_equity(tmp_path):
    security_master_path = tmp_path / "rates_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["ZN=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        rates_curve_symbols=("ZN=F",),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"ZN=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    for value in range(170, 109, -1):
        strategy._closes["ZN=F"].append(float(value))
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    event = MarketEvent(
        timestamp=datetime(2020, 6, 1),
        bars={
            "ZN=F": Bar("ZN=F", datetime(2020, 6, 1), 110.0, 111.0, 109.0, 110.0, 1_000.0, 110.0),
            "IEF": Bar("IEF", datetime(2020, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_FUTURE" if symbol == "ZN=F" else "DURATION",
            reason="UNUSED",
        )
        for symbol in ("ZN=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": 0.0, "inflation": 2.0, "policy": 1.5, "credit": 0.0, "liquidity": 0.0}},
        market_event=event,
    )

    assert out["ZN=F"].rates_curve_target_weight < 0.0
    assert out["IEF"].rates_curve_target_weight == 0.0
    assert strategy._last_rates_curve_futures_count == 1
    assert strategy._last_rates_curve_proxy_count == 0
    assert strategy._last_rates_curve_zero_contract_fallback_count == 0


def test_barbell_extreme_phase_skip_starts_rare_entries_at_phase_two(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_extreme_phase_skip_layer=True,
    )
    bar = Bar("SPY", datetime(2020, 3, 16), 240.0, 245.0, 235.0, 240.0, 1_000_000.0, 240.0)
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=-0.50,
        score=-2.80,
        region="US",
        theme="DISLOCATION",
        asset_class="Equity_Index",
        reason="ENGINE_B_ENTRY",
        core_target_weight=-0.50,
        coherence=0.90,
        extreme_regime=True,
    )

    phase, multiplier, stop_mult = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)

    assert phase == 2
    assert multiplier == pytest.approx(strategy.engine_b_phase2_weight_mult)
    assert stop_mult == pytest.approx(strategy.engine_b_phase2_stop_atr)
    assert strategy._last_engine_b_phase_skip_count == 1


def test_barbell_extreme_phase_skip_does_not_relax_normal_entries(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_extreme_phase_skip_layer=True,
    )
    bar = Bar("SPY", datetime(2020, 3, 16), 240.0, 245.0, 235.0, 240.0, 1_000_000.0, 240.0)
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=-0.50,
        score=-2.80,
        region="US",
        theme="DISLOCATION",
        asset_class="Equity_Index",
        reason="ENGINE_B_ENTRY",
        core_target_weight=-0.50,
        coherence=0.60,
        extreme_regime=True,
    )

    phase, multiplier, stop_mult = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)

    assert phase == 1
    assert multiplier == pytest.approx(strategy.engine_b_phase1_weight_mult)
    assert stop_mult == pytest.approx(strategy.engine_b_phase1_stop_atr)
    assert strategy._last_engine_b_phase_skip_count == 0


def test_barbell_rates_curve_inverts_yield_future_direction_for_duration_long(tmp_path):
    security_master_path = tmp_path / "yield_rates_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "10Y=F,2021-08-16,,Rates,10Y Treasury Yield Future,US,Rates_Yield_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["10Y=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        rates_curve_symbols=("10Y=F",),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"10Y=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    for value in range(160, 99, -1):
        strategy._closes["10Y=F"].append(float(value) / 25.0)
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    event = MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "10Y=F": Bar("10Y=F", datetime(2022, 6, 1), 4.0, 4.1, 3.9, 4.0, 1_000.0, 4.0),
            "IEF": Bar("IEF", datetime(2022, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_YIELD_FUTURE" if symbol == "10Y=F" else "DURATION",
            reason="UNUSED",
        )
        for symbol in ("10Y=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": -2.0, "inflation": 0.0, "policy": 0.0, "credit": -1.0, "liquidity": -1.0}},
        market_event=event,
    )

    assert out["10Y=F"].rates_curve_target_weight < 0.0
    assert out["10Y=F"].rates_curve_reason == "RATES_CURVE_DURATION_LONG_YIELD"
    assert out["IEF"].rates_curve_target_weight == 0.0


def test_barbell_rates_curve_falls_back_from_missing_yield_future_to_standard_future(tmp_path):
    security_master_path = tmp_path / "yield_standard_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "10Y=F,2021-08-16,,Rates,10Y Treasury Yield Future,US,Rates_Yield_Future",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["10Y=F", "ZN=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        rates_curve_symbols=("10Y=F", "ZN=F"),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"10Y=F": 1_000.0, "ZN=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    for value in range(170, 109, -1):
        strategy._closes["ZN=F"].append(float(value))
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    event = MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "ZN=F": Bar("ZN=F", datetime(2022, 6, 1), 110.0, 111.0, 109.0, 110.0, 1_000.0, 110.0),
            "IEF": Bar("IEF", datetime(2022, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_FUTURE",
            reason="UNUSED",
        )
        for symbol in ("10Y=F", "ZN=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": 0.0, "inflation": 2.0, "policy": 1.5, "credit": 0.0, "liquidity": 0.0}},
        market_event=event,
    )

    assert out["10Y=F"].rates_curve_target_weight == 0.0
    assert out["ZN=F"].rates_curve_target_weight < 0.0
    assert out["ZN=F"].rates_curve_reason == "RATES_CURVE_DURATION_SHORT_STANDARD"
    assert out["IEF"].rates_curve_target_weight == 0.0
    assert strategy._last_rates_curve_futures_count == 1
    assert strategy._last_rates_curve_proxy_count == 0


def test_barbell_rates_curve_uses_bond_future_for_duration_long_before_yield_proxy(tmp_path):
    security_master_path = tmp_path / "yield_index_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "10Y=F,2021-08-16,,Rates,10Y Treasury Yield Future,US,Rates_Yield_Future",
                "^TNX,1988-01-01,,Rates,10Y Treasury Yield Index Proxy,US,Rates_Yield_Future",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["10Y=F", "^TNX", "ZN=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        rates_curve_symbols=("10Y=F", "^TNX", "ZN=F"),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"10Y=F": 1_000.0, "^TNX": 1_000.0, "ZN=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    for value in range(50, 111):
        strategy._closes["^TNX"].append(float(value) / 25.0)
    for value in range(50, 111):
        strategy._closes["ZN=F"].append(float(value))
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    event = MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "^TNX": Bar("^TNX", datetime(2022, 6, 1), 4.5, 4.6, 4.4, 4.5, 1_000.0, 4.5),
            "ZN=F": Bar("ZN=F", datetime(2022, 6, 1), 112.0, 113.0, 111.0, 112.0, 1_000.0, 112.0),
            "IEF": Bar("IEF", datetime(2022, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_YIELD_FUTURE" if symbol in {"10Y=F", "^TNX"} else "RATES_FUTURE",
            reason="UNUSED",
        )
        for symbol in ("10Y=F", "^TNX", "ZN=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": -2.0, "inflation": 0.0, "policy": 0.0, "credit": -1.0, "liquidity": -1.0}},
        market_event=event,
    )

    assert out["10Y=F"].rates_curve_target_weight == 0.0
    assert out["^TNX"].rates_curve_target_weight == 0.0
    assert out["ZN=F"].rates_curve_target_weight > 0.0
    assert out["IEF"].rates_curve_target_weight == 0.0
    assert out["ZN=F"].rates_curve_reason == "RATES_CURVE_DURATION_LONG"


def test_barbell_rates_curve_uses_yield_index_proxy_for_duration_short(tmp_path):
    security_master_path = tmp_path / "yield_index_short_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "10Y=F,2021-08-16,,Rates,10Y Treasury Yield Future,US,Rates_Yield_Future",
                "^TNX,1988-01-01,,Rates,10Y Treasury Yield Index Proxy,US,Rates_Yield_Future",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["10Y=F", "^TNX", "ZN=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        rates_curve_symbols=("10Y=F", "^TNX", "ZN=F"),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"10Y=F": 1_000.0, "^TNX": 1_000.0, "ZN=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=250_000.0))
    for value in range(50, 111):
        strategy._closes["^TNX"].append(float(value) / 25.0)
    for value in range(170, 109, -1):
        strategy._closes["ZN=F"].append(float(value))
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    event = MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "^TNX": Bar("^TNX", datetime(2022, 6, 1), 4.5, 4.6, 4.4, 4.5, 1_000.0, 4.5),
            "ZN=F": Bar("ZN=F", datetime(2022, 6, 1), 110.0, 111.0, 109.0, 110.0, 1_000.0, 110.0),
            "IEF": Bar("IEF", datetime(2022, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_YIELD_FUTURE" if symbol in {"10Y=F", "^TNX"} else "RATES_FUTURE",
            reason="UNUSED",
        )
        for symbol in ("10Y=F", "^TNX", "ZN=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": 0.0, "inflation": 2.0, "policy": 1.5, "credit": 0.0, "liquidity": 0.0}},
        market_event=event,
    )

    assert out["10Y=F"].rates_curve_target_weight == 0.0
    assert out["^TNX"].rates_curve_target_weight > 0.0
    assert out["ZN=F"].rates_curve_target_weight == 0.0
    assert out["IEF"].rates_curve_target_weight == 0.0
    assert out["^TNX"].rates_curve_reason == "RATES_CURVE_DURATION_SHORT_YIELD_STANDARD"


def test_barbell_rates_rolling_ev_gate_blocks_negative_history(tmp_path):
    security_master_path = tmp_path / "rates_ev_gate_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "10Y=F,2021-08-16,,Rates,10Y Treasury Yield Future,US,Rates_Yield_Future",
                "^TNX,1988-01-01,,Rates,10Y Treasury Yield Index Proxy,US,Rates_Yield_Future",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "IEF,2002-07-22,,Rates,7-10Y Treasury ETF,US,Duration",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["10Y=F", "^TNX", "ZN=F", "IEF"],
        macro_source=DummyMacroSource(rows={}),
        security_master=CSVSecurityMasterDataSource(security_master_path),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_rates_curve_layer=True,
        enable_rates_rolling_ev_gate_layer=True,
        rates_rolling_ev_min_obs=5,
        rates_curve_symbols=("10Y=F", "^TNX", "ZN=F"),
        rates_proxy_symbols=("IEF",),
        instrument_multipliers={"10Y=F": 1_000.0, "^TNX": 1_000.0, "ZN=F": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=250_000.0))
    for value in range(50, 111):
        strategy._closes["^TNX"].append(float(value) / 25.0)
    for value in range(170, 109, -1):
        strategy._closes["ZN=F"].append(float(value))
    for value in range(100, 161):
        strategy._closes["IEF"].append(float(value))

    key = strategy._rates_rolling_ev_key("^TNX", "RATES_CURVE_DURATION_SHORT_YIELD_STANDARD")
    strategy._rates_rolling_ev_history[key] = deque([-0.01, -0.02, -0.01, -0.03, -0.02], maxlen=252)

    event = MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "^TNX": Bar("^TNX", datetime(2022, 6, 1), 4.5, 4.6, 4.4, 4.5, 1_000.0, 4.5),
            "ZN=F": Bar("ZN=F", datetime(2022, 6, 1), 110.0, 111.0, 109.0, 110.0, 1_000.0, 110.0),
            "IEF": Bar("IEF", datetime(2022, 6, 1), 100.0, 101.0, 99.0, 100.0, 1_000_000.0, 100.0),
        },
    )
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_YIELD_FUTURE" if symbol in {"10Y=F", "^TNX"} else "RATES_FUTURE",
            reason="UNUSED",
        )
        for symbol in ("10Y=F", "^TNX", "ZN=F", "IEF")
    }

    out = strategy._apply_rates_curve_sleeve(
        plans=plans,
        region_states={"US": {"growth": 0.0, "inflation": 2.0, "policy": 1.5, "credit": 0.0, "liquidity": 0.0}},
        market_event=event,
    )

    assert out["^TNX"].rates_curve_target_weight == 0.0
    assert strategy._last_rates_ev_gate_block_count == 1
    assert len(strategy._rates_rolling_ev_pending) == 1


def _write_curve_rv_security_master(path: Path) -> CSVSecurityMasterDataSource:
    path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "ZF=F,2000-01-01,,Rates,5Y Treasury Future,US,Rates_Future",
                "ZN=F,2000-01-01,,Rates,10Y Treasury Future,US,Rates_Future",
                "^FVX,1988-01-01,,Rates,5Y Treasury Yield Index Proxy,US,Rates_Yield_Future",
                "^TNX,1988-01-01,,Rates,10Y Treasury Yield Index Proxy,US,Rates_Yield_Future",
            ]
        ),
        encoding="ascii",
    )
    return CSVSecurityMasterDataSource(path)


def _curve_rv_plans() -> dict[str, BarbellTargetPlan]:
    return {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="UNASSIGNED",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="NONE",
            asset_class="RATES_YIELD_FUTURE" if symbol in {"^FVX", "^TNX"} else "RATES_FUTURE",
            reason="UNUSED",
        )
        for symbol in ("ZF=F", "ZN=F", "^FVX", "^TNX")
    }


def _curve_rv_event() -> MarketEvent:
    return MarketEvent(
        timestamp=datetime(2022, 6, 1),
        bars={
            "ZF=F": Bar("ZF=F", datetime(2022, 6, 1), 108.0, 109.0, 107.0, 108.0, 1_000.0, 108.0),
            "ZN=F": Bar("ZN=F", datetime(2022, 6, 1), 112.0, 113.0, 111.0, 112.0, 1_000.0, 112.0),
            "^FVX": Bar("^FVX", datetime(2022, 6, 1), 4.0, 4.1, 3.9, 4.0, 1_000.0, 4.0),
            "^TNX": Bar("^TNX", datetime(2022, 6, 1), 3.0, 3.1, 2.9, 3.0, 1_000.0, 3.0),
        },
    )


def test_barbell_curve_rv_bull_steepener_uses_front_long_back_short(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["ZF=F", "ZN=F", "^FVX", "^TNX"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_curve_rv_security_master(tmp_path / "curve_rv_security_master.csv"),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_curve_rv_layer=True,
        rates_curve_symbols=("ZF=F", "ZN=F", "^FVX", "^TNX"),
        rates_proxy_symbols=(),
        instrument_multipliers={"ZF=F": 1_000.0, "ZN=F": 1_000.0, "^FVX": 1_000.0, "^TNX": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    slopes = [0.8 - (2.2 * i / 239.0) for i in range(240)] + [-1.4 + (0.4 * i / 24.0) for i in range(25)]
    for slope in slopes:
        strategy._closes["^FVX"].append(4.0)
        strategy._closes["^TNX"].append(4.0 + slope)

    out = strategy._apply_curve_rv_sleeve(
        plans=_curve_rv_plans(),
        region_states={"US": {"growth": -1.0, "inflation": 0.0, "policy": -0.8, "credit": 0.0, "liquidity": 0.0}},
        market_event=_curve_rv_event(),
    )

    assert out["ZF=F"].curve_rv_target_weight > 0.0
    assert out["ZN=F"].curve_rv_target_weight < 0.0
    assert out["ZF=F"].curve_rv_reason == "CURVE_RV_BULL_STEEPENER_FUTURES"
    assert strategy._last_curve_rv_futures_count == 2


def test_barbell_curve_rv_bear_flattener_uses_front_short_back_long(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["ZF=F", "ZN=F", "^FVX", "^TNX"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_curve_rv_security_master(tmp_path / "curve_rv_security_master.csv"),
        carry_symbols=(),
        dislocation_symbols=(),
        enable_curve_rv_layer=True,
        rates_curve_symbols=("ZF=F", "ZN=F", "^FVX", "^TNX"),
        rates_proxy_symbols=(),
        instrument_multipliers={"ZF=F": 1_000.0, "ZN=F": 1_000.0, "^FVX": 1_000.0, "^TNX": 1_000.0},
    )
    strategy.bind_portfolio(Portfolio(initial_cash=10_000_000.0))
    slopes = [-0.8 + (2.2 * i / 239.0) for i in range(240)] + [1.4 - (0.4 * i / 24.0) for i in range(25)]
    for slope in slopes:
        strategy._closes["^FVX"].append(4.0)
        strategy._closes["^TNX"].append(4.0 + slope)

    out = strategy._apply_curve_rv_sleeve(
        plans=_curve_rv_plans(),
        region_states={"US": {"growth": 0.0, "inflation": 1.0, "policy": 1.0, "credit": 0.0, "liquidity": 0.0}},
        market_event=_curve_rv_event(),
    )

    assert out["ZF=F"].curve_rv_target_weight < 0.0
    assert out["ZN=F"].curve_rv_target_weight > 0.0
    assert out["ZF=F"].curve_rv_reason == "CURVE_RV_BEAR_FLATTENER_FUTURES"
    assert strategy._last_curve_rv_futures_count == 2


def test_macro_csv_source_preserves_event_driven_snapshots_with_snapshot_id(tmp_path):
    csv_path = tmp_path / "macro_event_driven.csv"
    csv_path.write_text(
        "\n".join(
            [
                "date,region,release_date,tradable_from,growth_surprise,inflation_surprise,policy_surprise,liquidity_surprise,credit_surprise,snapshot_id,quality_score,coverage_ratio,source_label",
                "2024-01-01,US,2024-01-15,2024-01-22,0.4,0.1,0.0,0.2,0.1,us_20240122_a,0.8,0.6,FRED:PAYEMS",
                "2024-01-01,US,2024-01-18,2024-01-25,1.1,0.1,0.0,0.2,0.1,us_20240125_b,0.9,0.8,FRED:PAYEMS|FRED:CPIAUCSL",
                "2024-02-01,US,2024-02-15,2024-02-22,-0.2,0.0,0.1,0.3,-0.1,us_20240222_a,1.0,1.0,FRED:MULTI",
            ]
        ),
        encoding="ascii",
    )

    source = CSVMacroDataSource(csv_path)
    history = source.history("US", date(2024, 1, 26))
    assert len(history) == 2
    assert history[-1].snapshot_id == "us_20240125_b"
    assert history[-1].quality_score == pytest.approx(0.9)
    assert history[-1].coverage_ratio == pytest.approx(0.8)


def test_macro_surprise_csv_source_filters_by_tradable_from(tmp_path):
    csv_path = tmp_path / "macro_surprises.csv"
    csv_path.write_text(
        "\n".join(
            [
                "region,factor,release_ts,tradable_from,surprise_z,source_label",
                "US,growth,2024-02-02T08:30:00,2024-02-02T08:35:00,1.2,NFP",
                "US,policy,2024-03-20T14:00:00,2024-03-20T14:05:00,-1.5,FOMC",
            ]
        ),
        encoding="ascii",
    )
    source = CSVMacroSurpriseDataSource(csv_path)

    assert source.events("US", date(2024, 2, 1)) == []
    first = source.events("US", date(2024, 2, 2))
    assert len(first) == 1
    assert first[0].factor == "growth"
    assert first[0].surprise_z == pytest.approx(1.2)
    recent = source.events("US", date(2024, 3, 21), lookback_days=10)
    assert len(recent) == 1
    assert recent[0].factor == "policy"


def test_barbell_macro_surprise_state_decays_point_in_time_events(tmp_path):
    security_master = _write_security_master(tmp_path / "security_master.csv")
    surprise_csv = tmp_path / "macro_surprises.csv"
    surprise_csv.write_text(
        "\n".join(
            [
                "region,factor,release_ts,tradable_from,surprise_z,source_label",
                "US,growth,2024-02-02T08:30:00,2024-02-02T08:35:00,2.0,NFP",
            ]
        ),
        encoding="ascii",
    )
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        macro_surprise_source=CSVMacroSurpriseDataSource(surprise_csv),
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_macro_surprise_layer=True,
    )

    same_day = strategy._macro_surprise_state("US", date(2024, 2, 2))
    later = strategy._macro_surprise_state("US", date(2024, 2, 23))

    assert same_day["impulses"]["growth"] == pytest.approx(2.0)  # type: ignore[index]
    assert 0.0 < later["impulses"]["growth"] < 2.0  # type: ignore[index]


def test_public_macro_builder_emits_event_driven_snapshots_and_global(tmp_path):
    events_csv = tmp_path / "macro_factor_events.csv"
    events_csv.write_text(
        "\n".join(
            [
                "region,factor,date,release_date,tradable_from,value,series,source,transform,weight",
                "US,growth,2024-01-01,2024-01-12,2024-01-19,0.6,PAYEMS,FRED:PAYEMS,zscore,1.0",
                "US,inflation,2024-01-01,2024-01-11,2024-01-18,0.2,CPI,FRED:CPI,zscore,1.0",
                "US,policy,2024-01-01,2024-01-31,2024-02-07,-0.1,FEDFUNDS,FRED:FEDFUNDS,zscore,1.0",
                "US,liquidity,2024-01-01,2024-01-24,2024-01-31,0.3,NFCI,FRED:NFCI,zscore,1.0",
                "US,credit,2024-01-01,2024-01-26,2024-02-02,0.1,OAS,FRED:OAS,zscore,1.0",
                "EUROPE,growth,2024-01-01,2024-01-15,2024-01-22,0.3,EU_IP,OECD:EU_IP,zscore,1.0",
                "EUROPE,inflation,2024-01-01,2024-01-16,2024-01-23,-0.1,EU_CPI,OECD:EU_CPI,zscore,1.0",
                "EUROPE,policy,2024-01-01,2024-01-30,2024-02-06,0.1,ECB,ECB:DFR,zscore,1.0",
                "EUROPE,liquidity,2024-01-01,2024-01-24,2024-01-31,0.2,EU_LIQ,ECB:LIQ,zscore,1.0",
                "EUROPE,credit,2024-01-01,2024-01-25,2024-02-01,0.0,EU_OAS,ICE:EU_OAS,zscore,1.0",
                "US,growth,2024-02-01,2024-02-12,2024-02-20,0.1,PAYEMS,FRED:PAYEMS,zscore,1.0",
                "EUROPE,growth,2024-02-01,2024-02-15,2024-02-22,-0.2,EU_IP,OECD:EU_IP,zscore,1.0",
            ]
        ),
        encoding="ascii",
    )

    frame = build_public_macro_snapshot_frame(
        events_csv,
        config=PublicMacroBuilderConfig(min_observations=2),
    )

    assert not frame.empty
    assert {"US", "EUROPE", "GLOBAL"}.issubset(set(frame["region"]))
    assert frame["snapshot_id"].is_unique
    assert frame["coverage_ratio"].between(0.0, 1.0).all()
    assert frame["quality_score"].between(0.0, 1.0).all()
    us_rows = frame[frame["region"] == "US"]
    assert len(us_rows) >= 5


def test_public_macro_builder_emits_surprise_proxy_events(tmp_path):
    events_csv = tmp_path / "macro_factor_events.csv"
    lines = ["region,factor,date,release_date,tradable_from,value,series,source,transform,weight"]
    for idx in range(8):
        month = idx + 1
        value = 100.0 + idx
        if idx == 7:
            value = 120.0
        lines.append(
            f"US,growth,2024-{month:02d}-01,2024-{month:02d}-10,2024-{month:02d}-15,{value},PAYEMS,FRED:PAYEMS,diff_1,1.0"
        )
    events_csv.write_text("\n".join(lines), encoding="ascii")

    frame = build_macro_surprise_event_frame(
        events_csv,
        config=PublicMacroBuilderConfig(z_window=4, min_observations=3),
    )

    assert not frame.empty
    assert list(frame.columns) == ["region", "factor", "release_ts", "tradable_from", "surprise_z", "source_label"]
    assert set(frame["factor"]) == {"growth"}
    assert frame["surprise_z"].abs().max() > 0.0
    assert (pd.to_datetime(frame["tradable_from"]) >= pd.to_datetime(frame["release_ts"])).all()


def test_public_macro_series_config_builds_factor_events_from_local_csv(tmp_path):
    series_csv = tmp_path / "payems.csv"
    series_csv.write_text(
        "\n".join(
            [
                "DATE,VALUE,realtime_start",
                "2024-01-01,156100,2024-02-02",
                "2024-02-01,156250,2024-03-08",
            ]
        ),
        encoding="ascii",
    )
    config_csv = tmp_path / "macro_public_config.csv"
    config_csv.write_text(
        "\n".join(
            [
                "region,factor,provider,series,source,path,date_col,value_col,release_date_col,transform,weight,scale,publication_lag_business_days",
                f"US,growth,LOCAL_CSV,PAYEMS,FRED:PAYEMS,{series_csv},DATE,VALUE,realtime_start,diff_1,1.0,1.0,3",
            ]
        ),
        encoding="ascii",
    )

    specs = load_public_macro_series_specs(config_csv)
    assert len(specs) == 1
    raw = fetch_public_macro_series(specs[0], cache_dir=tmp_path / "cache")
    normalized = normalize_public_macro_series_frame(raw, specs[0])
    assert normalized.iloc[0]["release_date"].date() == date(2024, 2, 2)
    assert normalized.iloc[0]["tradable_from"].date() == date(2024, 2, 7)

    events = build_public_macro_events_from_config(config_csv, cache_dir=tmp_path / "cache")
    assert len(events) == 2
    assert set(events["factor"]) == {"growth"}
    assert events.iloc[-1]["series"] == "PAYEMS"


def test_public_macro_series_config_uses_derived_release_lag_when_missing_dates(tmp_path):
    series_csv = tmp_path / "nfci.csv"
    series_csv.write_text(
        "\n".join(
            [
                "date,value",
                "2024-01-03,0.2",
                "2024-01-10,0.1",
            ]
        ),
        encoding="ascii",
    )
    config_csv = tmp_path / "macro_public_config.csv"
    config_csv.write_text(
        "\n".join(
            [
                "region,factor,provider,series,source,path,date_col,value_col,transform,weight,scale,release_lag_days,publication_lag_business_days",
                f"US,liquidity,LOCAL_CSV,NFCI,FRED:NFCI,{series_csv},date,value,level,1.0,1.0,2,2",
            ]
        ),
        encoding="ascii",
    )

    events = build_public_macro_events_from_config(config_csv, cache_dir=tmp_path / "cache")
    first = events.iloc[0]
    assert pd.Timestamp(first["release_date"]).date() == date(2024, 1, 5)
    assert pd.Timestamp(first["tradable_from"]).date() == date(2024, 1, 9)


def test_public_macro_series_config_fred_provider_requires_api_key(tmp_path):
    config_csv = tmp_path / "macro_public_config.csv"
    config_csv.write_text(
        "\n".join(
            [
                "region,factor,provider,series,source",
                "US,growth,FRED_API,PAYEMS,FRED:PAYEMS",
            ]
        ),
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="API key"):
        build_public_macro_events_from_config(config_csv, cache_dir=tmp_path / "cache")


def test_fred_market_series_does_not_treat_query_realtime_start_as_release_date(tmp_path):
    config_csv = tmp_path / "config.csv"
    config_csv.write_text(
        "region,factor,provider,series,source,release_lag_days,publication_lag_business_days\n"
        "US,credit,FRED_API,OAS,FRED:OAS,0,1\n",
        encoding="ascii",
    )
    spec = load_public_macro_series_specs(config_csv)[0]
    raw = pd.DataFrame(
        {"date": ["2008-09-15"], "value": [7.5], "realtime_start": ["2026-07-15"]}
    )

    normalized = normalize_public_macro_series_frame(raw, spec)

    assert pd.Timestamp(normalized.iloc[0]["release_date"]).date() == date(2008, 9, 15)
    assert pd.Timestamp(normalized.iloc[0]["tradable_from"]).date() == date(2008, 9, 16)


def test_default_public_macro_config_requests_alfred_initial_releases():
    config = Path("examples/data_templates/macro_public_series_template.csv")
    specs = load_public_macro_series_specs(config)

    alfred = [spec for spec in specs if spec.provider == "ALFRED_API"]
    fred = [spec for spec in specs if spec.provider == "FRED_API"]
    assert alfred
    assert all(spec.output_type == 4 for spec in alfred)
    assert all(spec.output_type == 1 for spec in fred)
    assert all(not spec.release_date_col for spec in fred)


def test_public_macro_series_config_alfred_provider_uses_first_release_query(tmp_path, monkeypatch):
    config_csv = tmp_path / "macro_public_config.csv"
    config_csv.write_text(
        "\n".join(
            [
                "region,factor,provider,series,source,observation_start,output_type",
                "US,growth,ALFRED_API,PAYEMS,ALFRED:PAYEMS,2000-01-01,4",
            ]
        ),
        encoding="ascii",
    )

    captured: dict[str, str] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return (
                b'{'
                b'"observations":['
                b'{"date":"2024-01-01","value":"100","realtime_start":"2024-01-12","realtime_end":"2024-01-12"},'
                b'{"date":"2024-02-01","value":"102","realtime_start":"2024-02-09","realtime_end":"2024-02-09"}'
                b']'
                b'}'
            )

    def _fake_urlopen(url: str, timeout: int = 30):
        captured["url"] = url
        return _Response()

    monkeypatch.setattr("quantbt.altdata.macro_public_ingest.open_url", _fake_urlopen)

    events = build_public_macro_events_from_config(
        config_csv,
        cache_dir=tmp_path / "cache",
        fred_api_key="demo-key",
        refresh=True,
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["series_id"] == ["PAYEMS"]
    assert query["output_type"] == ["4"]
    assert query["realtime_start"] == ["1776-07-04"]
    assert query["realtime_end"] == ["9999-12-31"]
    assert query["observation_start"] == ["2000-01-01"]
    assert len(events) == 2
    assert pd.Timestamp(events.iloc[0]["release_date"]).date() == date(2024, 1, 12)


def test_global_macro_cache_invalidates_on_new_macro_snapshot_id(tmp_path):
    security_master = _write_security_master(tmp_path / "security_master.csv")
    strategy = TopDownGlobalMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(
            rows={
                "US": [
                    MacroSnapshot("US", date(2024, 1, 1), date(2024, 1, 10), date(2024, 1, 17), 0.1, 0.0, 0.0, 0.0, 0.0, "us_a"),
                    MacroSnapshot("US", date(2024, 2, 1), date(2024, 2, 10), date(2024, 2, 16), 0.2, 0.0, 0.0, 0.0, 0.0, "us_b"),
                    MacroSnapshot("US", date(2024, 3, 1), date(2024, 3, 10), date(2024, 3, 15), 0.3, 0.0, 0.0, 0.0, 0.0, "us_c"),
                    MacroSnapshot("US", date(2024, 3, 1), date(2024, 3, 18), date(2024, 3, 22), 1.2, 0.0, 0.0, 0.0, 0.0, "us_d"),
                ]
            }
        ),
        security_master=security_master,
        z_window=3,
        z_window_max=3,
        min_macro_observations=2,
    )

    earlier = strategy._region_macro_state("US", date(2024, 3, 18))
    later = strategy._region_macro_state("US", date(2024, 3, 25))

    assert earlier is not None
    assert later is not None
    assert earlier["snapshot"].snapshot_id == "us_c"  # type: ignore[index]
    assert later["snapshot"].snapshot_id == "us_d"  # type: ignore[index]
    assert later["zscores"]["growth"] != earlier["zscores"]["growth"]  # type: ignore[index]


def test_global_macro_needs_rebalance_on_first_call(tmp_path):
    security_master = _write_security_master(tmp_path / "security_master.csv")
    strategy = TopDownGlobalMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
    )

    assert strategy._needs_rebalance(date(2024, 1, 2)) is True
    assert strategy._needs_rebalance(date(2024, 1, 3)) is False


def test_barbell_market_evidence_direction_comes_from_price_not_macro(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        dislocation_symbols=("SPY",),
        carry_symbols=(),
        enable_market_evidence_layer=True,
    )
    for idx in range(320):
        strategy._closes["SPY"].append(100.0 + 0.20 * idx + 0.08 * (idx % 7))

    score, _, _, reason, coherence, velocity = strategy._engine_b_score(
        symbol="SPY",
        as_of=date(2024, 1, 2),
        sector_map={"SPY": "MARKET"},
        region_map={"SPY": "US"},
        asset_map={"SPY": "EQUITY_INDEX"},
        region_states={
            "US": {"growth": -3.0, "inflation": 3.0, "policy": 3.0, "liquidity": -3.0, "credit": -3.0}
        },
        region_snapshots={},
        region_themes={"US": "STAGFLATION"},
        region_velocity_signals={"US": 0.0},
        relative_overlays={"SPY": -3.0},
    )

    assert score > 0.0
    assert reason == "MARKET_EVIDENCE_TREND"
    assert coherence == pytest.approx(1.0)
    assert velocity == pytest.approx(abs(score))


def test_barbell_market_evidence_carry_shutoff_ignores_disputed_macro_credit_sign(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_credit_security_master(tmp_path / "security_master.csv"),
        dislocation_symbols=(),
        enable_market_evidence_layer=True,
    )
    strategy.bind_portfolio(Portfolio(initial_cash=100_000.0))

    triggered, _ = strategy._update_engine_a_state(
        as_of=date(2024, 1, 2),
        region_states={"US": {"credit": -9.0}},
        bars={},
        equity=100_000.0,
    )

    assert triggered is False
    assert strategy._engine_a_shutoff_active is False


def test_barbell_market_evidence_requires_shock_for_entry_but_not_for_hold(tmp_path, monkeypatch):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        dislocation_symbols=("SPY",),
        carry_symbols=(),
        enable_market_evidence_layer=True,
    )
    monkeypatch.setattr(
        strategy,
        "_market_evidence_state",
        lambda _: {
            "score": 0.50,
            "agreement": 1.0,
            "velocity": 0.50,
            "daily_vol": 0.01,
            "short_score": 0.20,
            "medium_score": 0.30,
        },
    )
    kwargs = {
        "symbol": "SPY",
        "as_of": date(2024, 1, 2),
        "sector_map": {"SPY": "MARKET"},
        "region_map": {"SPY": "US"},
        "asset_map": {"SPY": "EQUITY_INDEX"},
        "region_states": {},
        "region_snapshots": {},
        "region_themes": {},
        "region_velocity_signals": {},
        "relative_overlays": {},
    }

    entry_score, *_, entry_reason, _, _ = strategy._engine_b_score(**kwargs)
    strategy._engine_b_state["SPY"] = {"sign": 1}
    hold_score, *_, hold_reason, _, _ = strategy._engine_b_score(**kwargs)

    assert entry_score == 0.0
    assert entry_reason == "MARKET_EVIDENCE_NO_SHOCK"
    assert hold_score == pytest.approx(strategy.engine_b_probe_threshold)
    assert hold_reason == "MARKET_EVIDENCE_HOLD"


def test_barbell_can_disable_engine_b_core_without_removing_dislocation_plans(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        dislocation_symbols=("SPY",),
        carry_symbols=(),
        enable_market_evidence_layer=True,
        enable_engine_b_core=False,
    )
    strategy.bind_portfolio(Portfolio(initial_cash=100_000.0))

    plans = strategy._build_barbell_plans(
        as_of=date(2024, 1, 2),
        sector_map={"SPY": "MARKET"},
        region_map={"SPY": "US"},
        asset_map={"SPY": "EQUITY_INDEX"},
        region_states={},
        region_snapshots={},
        region_themes={},
        region_velocity_signals={},
        market_event=MarketEvent(
            timestamp=datetime(2024, 1, 2),
            bars={"SPY": Bar("SPY", datetime(2024, 1, 2), 100.0, 101.0, 99.0, 100.0, 1_000_000)},
        ),
    )

    assert plans["SPY"].engine == "UNASSIGNED"
    assert plans["SPY"].target_weight == 0.0
    assert strategy.diagnostics()["engine_b_core_enabled"] is False


def test_barbell_cheap_discovery_only_reduces_phase1_size(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        dislocation_symbols=("SPY",),
        carry_symbols=(),
        enable_phase_stop_layer=True,
        enable_cheap_discovery_layer=True,
    )

    assert strategy.engine_b_phase1_weight_mult == pytest.approx(0.15)
    assert strategy.engine_b_phase2_weight_mult == pytest.approx(1.0)
    assert strategy.engine_b_phase3_weight_mult == pytest.approx(1.3)
    assert strategy.diagnostics()["cheap_discovery_enabled"] is True


def test_barbell_curve_commodity_risk_budget_is_volatility_scaled(tmp_path):
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=_write_security_master(tmp_path / "security_master.csv"),
        carry_symbols=(),
    )

    annualized_vol = 0.02 * (252.0**0.5)
    cap = min(
        strategy.commodity_futures_max_symbol_weight,
        strategy.commodity_curve_annual_risk_target / annualized_vol,
    )

    assert cap == pytest.approx(0.1259881577)
    assert cap < strategy.commodity_futures_price_only_max_symbol_weight


def test_global_macro_generates_entry_and_trailing_stop(tmp_path):
    security_master = _write_security_master(tmp_path / "security_master.csv")
    macro_rows = {
        "US": [
            MacroSnapshot("US", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.10, 0.05, 0.00, 0.10, 0.05),
            MacroSnapshot("US", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.20, 0.05, 0.00, 0.15, 0.05),
            MacroSnapshot("US", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.30, 0.05, 0.00, 0.20, 0.05),
            MacroSnapshot("US", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 1.20, 0.10, -0.05, 0.35, 0.20),
            MacroSnapshot("US", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 0.80, 0.10, -0.05, 0.25, 0.15),
        ]
    }
    strategy = TopDownGlobalMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(macro_rows),
        security_master=security_master,
        atr_period=5,
        trend_ma=20,
        z_window=4,
        min_macro_observations=3,
        max_theme_expressions=1,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)

    pending = []
    emitted = []
    index = pd.date_range("2020-03-01", periods=90, freq="D")
    close = 100.0

    for idx, ts in enumerate(index):
        if idx < 70:
            close += 0.6
        elif idx == 70:
            close -= 18.0
        else:
            close -= 0.3
        open_price = close - 0.2
        high = close + 0.8
        low = close - 0.8
        bar = Bar(
            symbol="SPY",
            timestamp=ts.to_pydatetime(),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1_000_000.0,
            adj_close=close,
        )

        for signal in pending:
            portfolio.on_fill(
                FillEvent(
                    timestamp=bar.timestamp,
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=int(signal.quantity or 0),
                    price=bar.open,
                    commission=0.0,
                    slippage_cost=0.0,
                    order_id=signal.signal_id,
                    metadata=signal.metadata,
                )
            )
        pending = []

        portfolio.mark_to_market(bar.timestamp, {"SPY": bar})
        signals = strategy.on_data(MarketEvent(timestamp=bar.timestamp, bars={"SPY": bar}))
        emitted.extend(signals)
        pending.extend(signals)

    reasons = [signal.metadata.get("reason") for signal in emitted]
    assert "REGIME_ENTRY" in reasons
    assert "TRAILING_STOP" in reasons
    assert any(signal.side == Side.BUY for signal in emitted)


def test_global_macro_uses_carry_layer_in_dead_band(tmp_path):
    security_master = _write_credit_security_master(tmp_path / "credit_security_master.csv")
    macro_rows = {
        "US": [
            MacroSnapshot("US", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.05, 0.00, 0.00, 0.03, 0.02),
            MacroSnapshot("US", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.06, 0.01, 0.00, 0.04, 0.03),
            MacroSnapshot("US", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.04, 0.00, 0.01, 0.02, 0.02),
            MacroSnapshot("US", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.05, 0.00, 0.01, 0.03, 0.03),
            MacroSnapshot("US", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 0.04, -0.01, 0.00, 0.03, 0.02),
        ]
    }
    strategy = TopDownGlobalMacroWizardStrategy(
        symbols=["HYG", "LQD"],
        macro_source=DummyMacroSource(macro_rows),
        security_master=security_master,
        atr_period=5,
        trend_ma=20,
        z_window=4,
        min_macro_observations=3,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)

    emitted = []
    index = pd.date_range("2020-03-01", periods=90, freq="D")
    closes = {"HYG": 80.0, "LQD": 120.0}
    for ts in index:
        bars = {}
        for symbol in closes:
            closes[symbol] += 0.15
            close = closes[symbol]
            bars[symbol] = Bar(
                symbol=symbol,
                timestamp=ts.to_pydatetime(),
                open=close - 0.1,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                volume=1_000_000.0,
                adj_close=close,
            )
        portfolio.mark_to_market(ts.to_pydatetime(), bars)
        signals = strategy.on_data(MarketEvent(timestamp=ts.to_pydatetime(), bars=bars))
        emitted.extend(signals)

    reasons = {signal.metadata.get("reason") for signal in emitted}
    assert "CARRY_LAYER" in reasons or "MIXED_CARRY" in reasons


def test_barbell_macro_carry_shutoff_on_credit_stress(tmp_path):
    security_master = _write_credit_security_master(tmp_path / "credit_security_master.csv")
    macro_rows = {
        "US": [
            MacroSnapshot("US", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.10, 0.00, 0.00, 0.05, 0.10),
            MacroSnapshot("US", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.10, 0.00, 0.00, 0.04, 0.12),
            MacroSnapshot("US", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.08, 0.00, 0.00, 0.03, 0.08),
            MacroSnapshot("US", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.06, 0.00, 0.00, 0.02, 0.05),
        ]
    }
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD"],
        macro_source=DummyMacroSource(macro_rows),
        security_master=security_master,
        dislocation_symbols=(),
        z_window=4,
        min_macro_observations=3,
        carry_drawdown_lookback=5,
        carry_drawdown_stop=0.03,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)

    pending = []
    emitted = []
    index = pd.date_range("2020-03-01", periods=20, freq="D")
    closes = {"HYG": 80.0, "LQD": 120.0}
    for idx, ts in enumerate(index):
        if idx < 12:
            closes["HYG"] += 0.4
            closes["LQD"] += 0.2
        elif idx == 12:
            closes["HYG"] -= 4.5
            closes["LQD"] -= 0.3
        else:
            closes["HYG"] -= 0.2
            closes["LQD"] += 0.1

        bars = {}
        for symbol, close in closes.items():
            bars[symbol] = Bar(
                symbol=symbol,
                timestamp=ts.to_pydatetime(),
                open=close - 0.1,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                volume=1_000_000.0,
                adj_close=close,
            )

        for signal in pending:
            portfolio.on_fill(
                FillEvent(
                    timestamp=ts.to_pydatetime(),
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=int(signal.quantity or 0),
                    price=bars[signal.symbol].open,
                    commission=0.0,
                    slippage_cost=0.0,
                    order_id=signal.signal_id,
                    metadata=signal.metadata,
                )
            )
        pending = []

        portfolio.mark_to_market(ts.to_pydatetime(), bars)
        signals = strategy.on_data(MarketEvent(timestamp=ts.to_pydatetime(), bars=bars))
        emitted.extend(signals)
        pending.extend(signals)

    reasons = {signal.metadata.get("reason") for signal in emitted}
    assert "CARRY_BASE" in reasons
    assert "CARRY_SHUTOFF_HYG_DRAWDOWN" in reasons


def test_barbell_macro_dislocation_requires_high_conviction(tmp_path):
    security_master_path = tmp_path / "barbell_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
                "DBC,2006-02-03,,Commodities,Broad Commodities,GLOBAL,Commodity",
                "USO,2006-04-10,,Commodities,Crude Oil,GLOBAL,Commodity",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    macro_rows = {
        "US": [
            MacroSnapshot("US", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.10, 0.00, 0.00, 0.03, 0.05),
            MacroSnapshot("US", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.12, 0.01, 0.01, 0.04, 0.06),
            MacroSnapshot("US", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.11, 0.01, 0.00, 0.03, 0.05),
            MacroSnapshot("US", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.10, 0.00, 0.00, 0.03, 0.04),
            MacroSnapshot("US", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 2.00, -1.20, -0.80, 1.80, 1.50),
        ],
        "EM": [
            MacroSnapshot("EM", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.05, 0.00, 0.00, 0.02, 0.03),
            MacroSnapshot("EM", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.06, 0.00, 0.01, 0.02, 0.03),
            MacroSnapshot("EM", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.04, 0.00, 0.00, 0.01, 0.02),
            MacroSnapshot("EM", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.05, 0.00, 0.00, 0.01, 0.02),
            MacroSnapshot("EM", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 1.50, -0.80, -0.20, 1.10, 0.80),
        ],
        "GLOBAL": [
            MacroSnapshot("GLOBAL", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.04, 0.00, 0.00, 0.02, 0.02),
            MacroSnapshot("GLOBAL", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.05, 0.01, 0.00, 0.03, 0.03),
            MacroSnapshot("GLOBAL", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.04, 0.00, 0.00, 0.02, 0.02),
            MacroSnapshot("GLOBAL", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.05, 0.00, 0.00, 0.03, 0.03),
            MacroSnapshot("GLOBAL", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 1.80, 1.10, -0.30, 1.20, 0.70),
        ],
    }
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY", "EEM", "DBC", "USO", "UUP"],
        macro_source=DummyMacroSource(macro_rows),
        security_master=security_master,
        carry_symbols=(),
        z_window=4,
        min_macro_observations=3,
        engine_b_probe_threshold=0.80,
        engine_b_conviction_threshold=1.20,
        engine_b_max_threshold=1.80,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)

    emitted = []
    index = pd.date_range("2020-03-01", periods=90, freq="D")
    closes = {"SPY": 100.0, "EEM": 40.0, "DBC": 15.0, "USO": 10.0, "UUP": 25.0}
    for ts in index:
        bars = {}
        for symbol, close in closes.items():
            closes[symbol] += 0.2 if symbol != "UUP" else 0.05
            close = closes[symbol]
            bars[symbol] = Bar(
                symbol=symbol,
                timestamp=ts.to_pydatetime(),
                open=close - 0.1,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                volume=1_000_000.0,
                adj_close=close,
            )
        portfolio.mark_to_market(ts.to_pydatetime(), bars)
        signals = strategy.on_data(MarketEvent(timestamp=ts.to_pydatetime(), bars=bars))
        emitted.extend(signals)

    reasons = [signal.metadata.get("reason") for signal in emitted]
    engines = [signal.metadata.get("engine") for signal in emitted]
    assert "ENGINE_B" in engines
    assert "ENGINE_B_ENTRY" in reasons


def test_barbell_macro_engine_a_feedback_stress_lowers_short_threshold(tmp_path):
    security_master_path = tmp_path / "barbell_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY",),
    )

    history = [
        (ts.date(), pnl)
        for ts, pnl in zip(
            pd.date_range("2020-01-01", periods=30, freq="D"),
            [0.0] * 8 + [-250.0] * 8 + [-1_000.0] * 8 + [-5_000.0] * 6,
            strict=True,
        )
    ]
    strategy._engine_a_pnl_history.extend(history)

    feedback = strategy._engine_b_feedback_state(as_of=date(2020, 1, 29), equity=100_000.0)
    assert feedback["a_stress"] is True
    assert feedback["a_euphoria"] is False
    assert feedback["short_threshold_multiplier"] == 0.70
    assert feedback["long_threshold_multiplier"] == 1.0


def test_barbell_macro_engine_a_feedback_euphoria_lowers_long_threshold(tmp_path):
    security_master_path = tmp_path / "barbell_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY",),
    )

    history = [
        (ts.date(), pnl)
        for ts, pnl in zip(
            pd.date_range("2020-01-01", periods=30, freq="D"),
            [0.0] * 8 + [250.0] * 8 + [1_500.0] * 8 + [5_000.0] * 6,
            strict=True,
        )
    ]
    strategy._engine_a_pnl_history.extend(history)

    feedback = strategy._engine_b_feedback_state(as_of=date(2020, 1, 29), equity=100_000.0)
    assert feedback["a_stress"] is False
    assert feedback["a_euphoria"] is True
    assert feedback["short_threshold_multiplier"] == 1.0
    assert feedback["long_threshold_multiplier"] == 0.80


def test_barbell_macro_overlay_activates_for_lagging_slow_asset(tmp_path):
    security_master_path = tmp_path / "overlay_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "UUP"),
    )
    strategy._bar_index = 80
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)

    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        spy_close = 100.0 + 0.01 * ((idx % 3) - 1)
        uup_close = 100.0 - 0.08 * idx
        strategy._closes["SPY"].append(spy_close)
        strategy._closes["UUP"].append(uup_close)
        strategy._dates["SPY"].append(ts.date())
        strategy._dates["UUP"].append(ts.date())

    market_event = MarketEvent(
        timestamp=datetime(2020, 3, 1),
        bars={
            "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
            "UUP": Bar("UUP", datetime(2020, 3, 1), 85.0, 85.3, 84.7, 84.9, 1_000_000.0, 84.9),
        },
    )
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.4,
            region="US",
            theme="GOLDILOCKS",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
        ),
        "UUP": BarbellTargetPlan(
            symbol="UUP",
            engine="ENGINE_B",
            target_weight=-0.20,
            score=-1.2,
            region="US",
            theme="GOLDILOCKS",
            asset_class="FX",
            reason="DISLOCATION_THEME",
            core_target_weight=-0.20,
        ),
    }

    out = strategy._apply_transmission_overlay(
        plans=plans,
        market_event=market_event,
        prices={"SPY": 103.1, "UUP": 84.9},
        equity=100_000.0,
    )
    plan = out["SPY"]
    assert plan.overlay_target_weight > 0.0
    assert plan.target_weight > plan.core_target_weight
    assert plan.overlay_reason == "ENGINE_B_OVERLAY_ENTRY"


def test_barbell_macro_overlay_off_suppresses_transmission_overlay(tmp_path):
    security_master_path = tmp_path / "overlay_off_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY", "UUP"),
        overlay_off=True,
    )
    out = strategy._apply_transmission_overlay(
        plans={
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.30,
                score=1.4,
                region="US",
                theme="REFLATION",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.30,
            )
        },
        market_event=MarketEvent(timestamp=datetime(2020, 1, 1), bars={}),
        prices={"SPY": 100.0},
        equity=100_000.0,
    )

    assert out["SPY"].target_weight == pytest.approx(0.30)
    assert out["SPY"].overlay_target_weight == 0.0
    assert strategy._overlay_last_snapshot["SPY"]["reason"] == "OVERLAY_OFF"


def test_barbell_macro_overlay_preserves_extreme_regime_flag(tmp_path):
    security_master_path = tmp_path / "overlay_extreme_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "UUP"),
    )
    strategy._bar_index = 80
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)
    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        strategy._closes["SPY"].append(100.0 + 0.01 * ((idx % 3) - 1))
        strategy._closes["UUP"].append(100.0 - 0.08 * idx)
        strategy._dates["SPY"].append(ts.date())
        strategy._dates["UUP"].append(ts.date())

    out = strategy._apply_transmission_overlay(
        plans={
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.40,
                score=1.4,
                region="US",
                theme="GOLDILOCKS",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.40,
                extreme_regime=True,
            ),
            "UUP": BarbellTargetPlan(
                symbol="UUP",
                engine="ENGINE_B",
                target_weight=-0.20,
                score=-1.2,
                region="US",
                theme="GOLDILOCKS",
                asset_class="FX",
                reason="DISLOCATION_THEME",
                core_target_weight=-0.20,
            ),
        },
        market_event=MarketEvent(
            timestamp=datetime(2020, 3, 1),
            bars={
                "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
                "UUP": Bar("UUP", datetime(2020, 3, 1), 85.0, 85.3, 84.7, 84.9, 1_000_000.0, 84.9),
            },
        ),
        prices={"SPY": 103.1, "UUP": 84.9},
        equity=100_000.0,
    )

    assert out["SPY"].extreme_regime is True


def test_barbell_macro_overlay_routing_shifts_weight_from_weaker_same_sign_donor(tmp_path):
    security_master_path = tmp_path / "overlay_routing_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "EEM", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "EEM", "UUP"),
        enable_overlay_routing_layer=True,
    )
    strategy._bar_index = 80
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)

    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        strategy._closes["SPY"].append(100.0 + 0.01 * ((idx % 3) - 1))
        strategy._closes["UUP"].append(100.0 - 0.08 * idx)
        strategy._dates["SPY"].append(ts.date())
        strategy._dates["UUP"].append(ts.date())

    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.4,
            region="US",
            theme="GOLDILOCKS",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            core_phase=2,
        ),
        "EEM": BarbellTargetPlan(
            symbol="EEM",
            engine="ENGINE_B",
            target_weight=0.25,
            score=0.6,
            region="EM",
            theme="GOLDILOCKS",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.25,
            core_phase=1,
        ),
        "UUP": BarbellTargetPlan(
            symbol="UUP",
            engine="ENGINE_B",
            target_weight=-0.20,
            score=-1.2,
            region="US",
            theme="GOLDILOCKS",
            asset_class="FX",
            reason="DISLOCATION_THEME",
            core_target_weight=-0.20,
            core_phase=2,
        ),
    }
    before_gross = sum(abs(plan.target_weight) for plan in plans.values())

    out = strategy._apply_transmission_overlay(
        plans=plans,
        market_event=MarketEvent(
            timestamp=datetime(2020, 3, 1),
            bars={
                "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
                "EEM": Bar("EEM", datetime(2020, 3, 1), 45.0, 45.2, 44.8, 45.1, 1_000_000.0, 45.1),
                "UUP": Bar("UUP", datetime(2020, 3, 1), 85.0, 85.3, 84.7, 84.9, 1_000_000.0, 84.9),
            },
        ),
        prices={"SPY": 103.1, "EEM": 45.1, "UUP": 84.9},
        equity=100_000.0,
    )

    assert out["SPY"].overlay_target_weight > 0.0
    assert out["SPY"].target_weight > out["SPY"].core_target_weight
    assert out["EEM"].target_weight < 0.25
    assert out["EEM"].reason == "ENGINE_B_OVERLAY_ROUTE_OUT"
    after_gross = sum(abs(plan.target_weight) for plan in out.values())
    assert after_gross == pytest.approx(before_gross)


def test_barbell_macro_overlay_routing_does_not_cannibalize_phase_three_donor(tmp_path):
    security_master_path = tmp_path / "overlay_routing_phase3_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "EEM"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "EEM"),
        enable_overlay_routing_layer=True,
    )
    strategy._bar_index = 80
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)
    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        strategy._closes["SPY"].append(100.0 + 0.01 * ((idx % 3) - 1))
        strategy._dates["SPY"].append(ts.date())

    out = strategy._apply_transmission_overlay(
        plans={
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.40,
                score=1.4,
                region="US",
                theme="GOLDILOCKS",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.40,
                core_phase=2,
            ),
            "EEM": BarbellTargetPlan(
                symbol="EEM",
                engine="ENGINE_B",
                target_weight=0.25,
                score=0.6,
                region="EM",
                theme="GOLDILOCKS",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.25,
                core_phase=3,
            ),
        },
        market_event=MarketEvent(
            timestamp=datetime(2020, 3, 1),
            bars={
                "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
                "EEM": Bar("EEM", datetime(2020, 3, 1), 45.0, 45.2, 44.8, 45.1, 1_000_000.0, 45.1),
            },
        ),
        prices={"SPY": 103.1, "EEM": 45.1},
        equity=100_000.0,
    )

    assert out["SPY"].overlay_target_weight == 0.0
    assert out["SPY"].overlay_reason == "ENGINE_B_OVERLAY_NO_ROUTE"
    assert out["EEM"].target_weight == pytest.approx(0.25)


def test_barbell_macro_overlay_routing_can_fall_back_to_opposite_sign_donor(tmp_path):
    security_master_path = tmp_path / "overlay_routing_cross_sign_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "UUP"),
        enable_overlay_routing_layer=True,
    )
    strategy._bar_index = 80
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)
    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        strategy._closes["SPY"].append(100.0 + 0.01 * ((idx % 3) - 1))
        strategy._closes["UUP"].append(100.0 - 0.08 * idx)
        strategy._dates["SPY"].append(ts.date())
        strategy._dates["UUP"].append(ts.date())

    out = strategy._apply_transmission_overlay(
        plans={
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.40,
                score=1.4,
                region="US",
                theme="GOLDILOCKS",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.40,
                core_phase=2,
            ),
            "UUP": BarbellTargetPlan(
                symbol="UUP",
                engine="ENGINE_B",
                target_weight=-0.20,
                score=-0.4,
                region="US",
                theme="GOLDILOCKS",
                asset_class="FX",
                reason="DISLOCATION_THEME",
                core_target_weight=-0.20,
                core_phase=1,
            ),
        },
        market_event=MarketEvent(
            timestamp=datetime(2020, 3, 1),
            bars={
                "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
                "UUP": Bar("UUP", datetime(2020, 3, 1), 85.0, 85.3, 84.7, 84.9, 1_000_000.0, 84.9),
            },
        ),
        prices={"SPY": 103.1, "UUP": 84.9},
        equity=100_000.0,
    )

    assert out["SPY"].overlay_target_weight > 0.0
    assert out["UUP"].target_weight > -0.20
    assert out["UUP"].reason == "ENGINE_B_OVERLAY_ROUTE_OUT"


def test_barbell_macro_velocity_exit_zeroes_engine_b_target(tmp_path):
    security_master_path = tmp_path / "velocity_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_velocity_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 3, 1),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="seed-fill",
            metadata={},
        )
    )
    strategy._engine_b_state["SPY"] = {"sign": 1, "peak_score": 1.6, "peak_velocity": 1.8}
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.45,
            score=1.0,
            region="US",
            theme="REFLATION",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.45,
            coherence=0.7,
            velocity_signal=0.2,
            velocity_multiplier=1.3,
        )
    }

    out = strategy._apply_velocity_exits(plans)
    plan = out["SPY"]
    assert plan.target_weight == 0.0
    assert plan.core_target_weight == 0.0
    assert plan.reason == "ENGINE_B_VELOCITY_EXIT"


def test_barbell_macro_overlay_exits_on_velocity_deceleration(tmp_path):
    security_master_path = tmp_path / "overlay_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "UUP"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "UUP"),
        enable_velocity_layer=True,
    )
    strategy._bar_index = 40
    strategy._overlay_state["SPY"] = {"entry_bar": 0, "sign": 1, "peak_velocity": 1.8}
    strategy._engine_a_pnl_5d_ratio_history.extend([0.00035 + 0.00005 * (idx % 5) for idx in range(60)])
    strategy._last_engine_b_feedback["engine_a_pnl_5d_ratio"] = 0.00120
    strategy._overlay_leader_history["SPY"].extend([0.65] * 60)

    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=60, freq="D")):
        spy_close = 100.0 + 0.01 * ((idx % 3) - 1)
        uup_close = 100.0 - 0.08 * idx
        strategy._closes["SPY"].append(spy_close)
        strategy._closes["UUP"].append(uup_close)
        strategy._dates["SPY"].append(ts.date())
        strategy._dates["UUP"].append(ts.date())

    market_event = MarketEvent(
        timestamp=datetime(2020, 3, 1),
        bars={
            "SPY": Bar("SPY", datetime(2020, 3, 1), 103.0, 103.4, 102.7, 103.1, 1_000_000.0, 103.1),
            "UUP": Bar("UUP", datetime(2020, 3, 1), 85.0, 85.3, 84.7, 84.9, 1_000_000.0, 84.9),
        },
    )
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.4,
            region="US",
            theme="GOLDILOCKS",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            velocity_signal=0.2,
        ),
        "UUP": BarbellTargetPlan(
            symbol="UUP",
            engine="ENGINE_B",
            target_weight=-0.20,
            score=-1.2,
            region="US",
            theme="GOLDILOCKS",
            asset_class="FX",
            reason="DISLOCATION_THEME",
            core_target_weight=-0.20,
            velocity_signal=0.2,
        ),
    }

    out = strategy._apply_transmission_overlay(
        plans=plans,
        market_event=market_event,
        prices={"SPY": 103.1, "UUP": 84.9},
        equity=100_000.0,
    )
    plan = out["SPY"]
    assert plan.overlay_target_weight == 0.0
    assert plan.overlay_reason == "ENGINE_B_OVERLAY_VELOCITY_EXIT"


def test_barbell_macro_phase_layer_starts_core_at_half_size(tmp_path):
    security_master_path = tmp_path / "phase_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=40, freq="D")):
        close = 100.0 + 0.5 * idx
        strategy._highs["SPY"].append(close + 1.0)
        strategy._lows["SPY"].append(close - 1.0)
        strategy._closes["SPY"].append(close)
        strategy._dates["SPY"].append(ts.date())
    strategy._bar_index = 40
    market_event = MarketEvent(
        timestamp=datetime(2020, 2, 9),
        bars={
            "SPY": Bar("SPY", datetime(2020, 2, 9), 120.0, 121.0, 119.0, 120.5, 1_000_000.0, 120.5),
        },
    )
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.5,
            region="US",
            theme="REFLATION",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            coherence=0.8,
            velocity_signal=0.6,
        )
    }

    out, blocked = strategy._apply_phase_stop_architecture(plans=plans, market_event=market_event)
    plan = out["SPY"]
    assert not blocked
    assert plan.core_phase == 1
    assert plan.core_target_weight == 0.20
    assert plan.target_weight == 0.20


def test_barbell_macro_phase_stop_exits_core_on_early_failure(tmp_path):
    security_master_path = tmp_path / "phase_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 3, 1),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="phase-seed",
            metadata={},
        )
    )
    for idx, ts in enumerate(pd.date_range("2020-01-01", periods=40, freq="D")):
        close = 100.0 + 0.1 * idx
        strategy._highs["SPY"].append(close + 1.0)
        strategy._lows["SPY"].append(close - 1.0)
        strategy._closes["SPY"].append(close)
        strategy._dates["SPY"].append(ts.date())
    strategy._bar_index = 6
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 1.2,
        "peak_velocity": 0.8,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 1,
    }
    market_event = MarketEvent(
        timestamp=datetime(2020, 2, 10),
        bars={
            "SPY": Bar("SPY", datetime(2020, 2, 10), 96.2, 96.5, 95.8, 96.0, 1_000_000.0, 96.0),
        },
    )
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.1,
            region="US",
            theme="REFLATION",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            coherence=0.7,
            velocity_signal=0.4,
        )
    }

    out, blocked = strategy._apply_phase_stop_architecture(plans=plans, market_event=market_event)
    plan = out["SPY"]
    assert "SPY" in blocked
    assert plan.target_weight == 0.0
    assert plan.reason == "ENGINE_B_PHASE_STOP"


def test_barbell_macro_reallocation_routes_freed_carry_gross_to_phase_two_core(tmp_path):
    security_master_path = tmp_path / "reallocation_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 3, 1),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="reallocation-seed",
            metadata={},
        )
    )
    strategy._engine_a_shutoff_active = True
    plans = {
        "HYG": BarbellTargetPlan(
            symbol="HYG",
            engine="ENGINE_A",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="CARRY",
            asset_class="CREDIT",
            reason="CARRY_SHUTOFF_HYG_DRAWDOWN",
            core_target_weight=0.0,
        ),
        "LQD": BarbellTargetPlan(
            symbol="LQD",
            engine="ENGINE_A",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="CARRY",
            asset_class="CREDIT",
            reason="CARRY_SHUTOFF_HYG_DRAWDOWN",
            core_target_weight=0.0,
        ),
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.4,
            region="US",
            theme="REFLATION",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            core_phase=2,
        ),
    }

    out = strategy._apply_engine_a_shutoff_reallocation(plans=plans)
    plan = out["SPY"]
    assert plan.reallocation_target_weight == pytest.approx(0.30)
    assert plan.core_target_weight == pytest.approx(0.70)
    assert plan.target_weight == pytest.approx(0.70)
    assert strategy._last_engine_a_reallocation_gross == pytest.approx(0.30)
    assert strategy._last_engine_a_reallocation_count == 1


def test_barbell_macro_reallocation_skips_phase_one_positions(tmp_path):
    security_master_path = tmp_path / "reallocation_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 3, 1),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="reallocation-seed",
            metadata={},
        )
    )
    strategy._engine_a_shutoff_active = True
    plans = {
        "HYG": BarbellTargetPlan(
            symbol="HYG",
            engine="ENGINE_A",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="CARRY",
            asset_class="CREDIT",
            reason="CARRY_SHUTOFF_HYG_DRAWDOWN",
            core_target_weight=0.0,
        ),
        "LQD": BarbellTargetPlan(
            symbol="LQD",
            engine="ENGINE_A",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="CARRY",
            asset_class="CREDIT",
            reason="CARRY_SHUTOFF_HYG_DRAWDOWN",
            core_target_weight=0.0,
        ),
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.40,
            score=1.4,
            region="US",
            theme="REFLATION",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_THEME",
            core_target_weight=0.40,
            core_phase=1,
        ),
    }

    out = strategy._apply_engine_a_shutoff_reallocation(plans=plans)
    plan = out["SPY"]
    assert plan.reallocation_target_weight == 0.0
    assert plan.core_target_weight == pytest.approx(0.40)
    assert plan.target_weight == pytest.approx(0.40)
    assert strategy._last_engine_a_reallocation_gross == 0.0
    assert strategy._last_engine_a_reallocation_count == 0


def test_barbell_macro_focused_reallocation_routes_all_freed_gross_to_top_extreme_symbol(tmp_path):
    security_master_path = tmp_path / "focused_reallocation_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "EEM"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "EEM"),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
        enable_extreme_concentration_layer=True,
        enable_focused_reallocation_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    for symbol in ("SPY", "EEM"):
        portfolio.on_fill(
            FillEvent(
                timestamp=datetime(2020, 3, 1),
                symbol=symbol,
                side=Side.BUY,
                quantity=100,
                price=100.0,
                commission=0.0,
                slippage_cost=0.0,
                order_id=f"{symbol}-seed",
                metadata={},
            )
        )
    strategy._engine_a_shutoff_active = True
    out = strategy._apply_engine_a_shutoff_reallocation(
        plans={
            "HYG": BarbellTargetPlan(
                symbol="HYG",
                engine="ENGINE_A",
                target_weight=0.0,
                score=0.0,
                region="US",
                theme="CARRY",
                asset_class="CREDIT",
                reason="CARRY_SHUTOFF",
                core_target_weight=0.0,
            ),
            "LQD": BarbellTargetPlan(
                symbol="LQD",
                engine="ENGINE_A",
                target_weight=0.0,
                score=0.0,
                region="US",
                theme="CARRY",
                asset_class="CREDIT",
                reason="CARRY_SHUTOFF",
                core_target_weight=0.0,
            ),
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.40,
                score=1.8,
                region="US",
                theme="REFLATION",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.40,
                core_phase=2,
                extreme_regime=True,
            ),
            "EEM": BarbellTargetPlan(
                symbol="EEM",
                engine="ENGINE_B",
                target_weight=0.40,
                score=1.1,
                region="EM",
                theme="REFLATION",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.40,
                core_phase=2,
                extreme_regime=True,
            ),
        }
    )
    assert out["SPY"].reallocation_target_weight == pytest.approx(0.30)
    assert out["SPY"].target_weight == pytest.approx(0.70)
    assert out["EEM"].reallocation_target_weight == pytest.approx(0.0)
    assert out["EEM"].target_weight == pytest.approx(0.40)
    assert strategy._last_engine_a_reallocation_gross == pytest.approx(0.30)
    assert strategy._last_engine_a_reallocation_count == 1


def test_barbell_macro_focused_reallocation_falls_back_to_pro_rata_without_extreme_flags(tmp_path):
    security_master_path = tmp_path / "focused_reallocation_fallback_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "EEM"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        dislocation_symbols=("SPY", "EEM"),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
        enable_extreme_concentration_layer=True,
        enable_focused_reallocation_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    for symbol in ("SPY", "EEM"):
        portfolio.on_fill(
            FillEvent(
                timestamp=datetime(2020, 3, 1),
                symbol=symbol,
                side=Side.BUY,
                quantity=100,
                price=100.0,
                commission=0.0,
                slippage_cost=0.0,
                order_id=f"{symbol}-seed",
                metadata={},
            )
        )
    strategy._engine_a_shutoff_active = True
    out = strategy._apply_engine_a_shutoff_reallocation(
        plans={
            "HYG": BarbellTargetPlan(
                symbol="HYG",
                engine="ENGINE_A",
                target_weight=0.0,
                score=0.0,
                region="US",
                theme="CARRY",
                asset_class="CREDIT",
                reason="CARRY_SHUTOFF",
                core_target_weight=0.0,
            ),
            "LQD": BarbellTargetPlan(
                symbol="LQD",
                engine="ENGINE_A",
                target_weight=0.0,
                score=0.0,
                region="US",
                theme="CARRY",
                asset_class="CREDIT",
                reason="CARRY_SHUTOFF",
                core_target_weight=0.0,
            ),
            "SPY": BarbellTargetPlan(
                symbol="SPY",
                engine="ENGINE_B",
                target_weight=0.30,
                score=1.2,
                region="US",
                theme="REFLATION",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.30,
                core_phase=2,
                extreme_regime=False,
            ),
            "EEM": BarbellTargetPlan(
                symbol="EEM",
                engine="ENGINE_B",
                target_weight=0.30,
                score=1.0,
                region="EM",
                theme="REFLATION",
                asset_class="EQUITY_INDEX",
                reason="DISLOCATION_THEME",
                core_target_weight=0.30,
                core_phase=2,
                extreme_regime=False,
            ),
        }
    )
    assert out["SPY"].reallocation_target_weight == pytest.approx(0.15)
    assert out["EEM"].reallocation_target_weight == pytest.approx(0.15)
    assert strategy._last_engine_a_reallocation_gross == pytest.approx(0.30)
    assert strategy._last_engine_a_reallocation_count == 2


def test_barbell_macro_asymmetric_phase_advances_fast_crisis_expression_earlier(tmp_path):
    security_master_path = tmp_path / "asymmetric_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["UUP", "SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("UUP", "SPY"),
        enable_phase_stop_layer=True,
        enable_asymmetric_phase_timing_layer=True,
    )
    strategy._bar_index = 5
    bar = Bar("UUP", datetime(2020, 3, 6), 104.0, 104.5, 103.8, 104.0, 1_000_000.0, 104.0)
    strategy._engine_b_state["UUP"] = {
        "sign": 1,
        "peak_score": 1.5,
        "peak_velocity": 1.0,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 1,
    }
    plan = BarbellTargetPlan(
        symbol="UUP",
        engine="ENGINE_B",
        target_weight=0.30,
        score=1.5,
        region="US",
        theme="CRISIS",
        asset_class="FX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.30,
        coherence=0.8,
        velocity_signal=0.6,
    )

    phase, weight_mult, _ = strategy._core_phase_definition(symbol="UUP", plan=plan, bar=bar, atr=4.0)
    assert phase == 2
    assert weight_mult == pytest.approx(1.0)


def test_barbell_macro_asymmetric_phase_keeps_slow_expression_on_default_timing(tmp_path):
    security_master_path = tmp_path / "asymmetric_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_asymmetric_phase_timing_layer=True,
    )
    strategy._bar_index = 5
    bar = Bar("SPY", datetime(2020, 3, 6), 104.0, 104.5, 103.8, 104.0, 1_000_000.0, 104.0)
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 1.5,
        "peak_velocity": 1.0,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 1,
    }
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.30,
        score=1.5,
        region="US",
        theme="REFLATION",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.30,
        coherence=0.8,
        velocity_signal=0.6,
    )

    phase, weight_mult, _ = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)
    assert phase == 1
    assert weight_mult == pytest.approx(0.5)


def test_barbell_macro_phase3_profit_rate_promotes_fast_confirmed_trade_early(tmp_path):
    security_master = _write_security_master(tmp_path / "phase3_profit_rate_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_phase3_profit_rate_layer=True,
    )
    strategy._bar_index = 15
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 2.0,
        "peak_velocity": 0.8,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 2,
    }
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.40,
        score=2.0,
        region="US",
        theme="REFLATION",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.40,
        coherence=0.8,
        velocity_signal=0.6,
    )
    bar = Bar("SPY", datetime(2020, 4, 10), 113.0, 114.0, 112.0, 113.0, 1_000_000.0, 113.0)

    phase, weight_mult, _ = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)
    assert phase == 3
    assert weight_mult == pytest.approx(1.3)


def test_barbell_macro_phase3_profit_rate_blocks_slow_three_atr_grind(tmp_path):
    security_master = _write_security_master(tmp_path / "phase3_profit_rate_slow_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_phase3_profit_rate_layer=True,
    )
    strategy._bar_index = 30
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 2.0,
        "peak_velocity": 0.8,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 2,
    }
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.40,
        score=2.0,
        region="US",
        theme="REFLATION",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.40,
        coherence=0.8,
        velocity_signal=0.6,
    )
    bar = Bar("SPY", datetime(2020, 4, 10), 112.0, 113.0, 111.0, 112.0, 1_000_000.0, 112.0)

    phase, weight_mult, _ = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)
    assert phase == 2
    assert weight_mult == pytest.approx(1.0)


def test_barbell_macro_extreme_concentration_narrows_selection_to_top_two(tmp_path):
    security_master_path = tmp_path / "extreme_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "HYG,2007-04-11,,Credit,High Yield Credit,US,Credit",
                "LQD,2002-07-26,,Credit,Investment Grade Credit,US,Credit",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
                "DBC,2006-02-03,,Commodities,Broad Commodities,GLOBAL,Commodity",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    macro_rows = {
        "US": [
            MacroSnapshot("US", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("US", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.2, 0.1, 0.0, 0.1, 0.0),
            MacroSnapshot("US", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("US", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.2, 0.1, 0.0, 0.1, 0.0),
            MacroSnapshot("US", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 2.0, -0.8, -0.7, 1.4, 0.9),
        ],
        "EM": [
            MacroSnapshot("EM", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("EM", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.2, 0.0, 0.1, 0.0, 0.0),
            MacroSnapshot("EM", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("EM", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.2, 0.0, 0.1, 0.0, 0.0),
            MacroSnapshot("EM", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 1.5, -0.7, -0.3, 1.1, 0.7),
        ],
        "GLOBAL": [
            MacroSnapshot("GLOBAL", date(2020, 1, 1), date(2020, 1, 15), date(2020, 1, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("GLOBAL", date(2020, 2, 1), date(2020, 2, 15), date(2020, 2, 15), 0.2, 0.1, 0.0, 0.0, 0.0),
            MacroSnapshot("GLOBAL", date(2020, 3, 1), date(2020, 3, 15), date(2020, 3, 15), 0.1, 0.0, 0.0, 0.0, 0.0),
            MacroSnapshot("GLOBAL", date(2020, 4, 1), date(2020, 4, 15), date(2020, 4, 15), 0.2, 0.1, 0.0, 0.0, 0.0),
            MacroSnapshot("GLOBAL", date(2020, 5, 1), date(2020, 5, 15), date(2020, 5, 15), 1.8, 1.2, -0.4, 1.3, 0.6),
        ],
    }
    strategy = BarbellMacroWizardStrategy(
        symbols=["HYG", "LQD", "SPY", "EEM", "DBC"],
        macro_source=DummyMacroSource(macro_rows),
        security_master=security_master,
        dislocation_symbols=("SPY", "EEM", "DBC"),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
        enable_extreme_concentration_layer=True,
        z_window=4,
        min_macro_observations=3,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    strategy._engine_b_coherence_history.extend([0.30] * 60)
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 1.5,
        "peak_velocity": 0.6,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 2,
    }
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 5, 16),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="extreme-seed",
            metadata={},
        )
    )
    for idx, ts in enumerate(pd.date_range("2020-03-01", periods=80, freq="D")):
        strategy._highs["SPY"].append(100.0 + idx * 0.2 + 1.0)
        strategy._lows["SPY"].append(100.0 + idx * 0.2 - 1.0)
        strategy._closes["SPY"].append(100.0 + idx * 0.2)
        strategy._highs["EEM"].append(50.0 + idx * 0.15 + 1.0)
        strategy._lows["EEM"].append(50.0 + idx * 0.15 - 1.0)
        strategy._closes["EEM"].append(50.0 + idx * 0.15)
        strategy._highs["DBC"].append(20.0 + idx * 0.1 + 0.5)
        strategy._lows["DBC"].append(20.0 + idx * 0.1 - 0.5)
        strategy._closes["DBC"].append(20.0 + idx * 0.1)
        for symbol in ("SPY", "EEM", "DBC"):
            strategy._dates[symbol].append(ts.date())

    event = MarketEvent(
        timestamp=datetime(2020, 5, 20),
        bars={
            "SPY": Bar("SPY", datetime(2020, 5, 20), 116.0, 117.0, 115.0, 116.5, 1_000_000.0, 116.5),
            "EEM": Bar("EEM", datetime(2020, 5, 20), 62.0, 63.0, 61.0, 62.2, 1_000_000.0, 62.2),
            "DBC": Bar("DBC", datetime(2020, 5, 20), 28.0, 28.5, 27.5, 28.1, 1_000_000.0, 28.1),
            "HYG": Bar("HYG", datetime(2020, 5, 20), 80.0, 80.5, 79.5, 80.1, 1_000_000.0, 80.1),
            "LQD": Bar("LQD", datetime(2020, 5, 20), 120.0, 120.5, 119.5, 120.1, 1_000_000.0, 120.1),
        },
    )

    plans = strategy._build_barbell_plans(
        as_of=date(2020, 5, 20),
        sector_map=security_master.sector_map(date(2020, 5, 20)),
        region_map=security_master.region_map(date(2020, 5, 20)),
        asset_map=security_master.asset_class_map(date(2020, 5, 20)),
        region_states={"US": {"growth": 2.0, "inflation": -0.8, "policy": -0.7, "liquidity": 1.4, "credit": 0.9},
                       "EM": {"growth": 1.5, "inflation": -0.7, "policy": -0.3, "liquidity": 1.1, "credit": 0.7},
                       "GLOBAL": {"growth": 1.8, "inflation": 1.2, "policy": -0.4, "liquidity": 1.3, "credit": 0.6}},
        region_snapshots={
            "US": macro_rows["US"][-1],
            "EM": macro_rows["EM"][-1],
            "GLOBAL": macro_rows["GLOBAL"][-1],
        },
        region_themes={"US": "REFLATION", "EM": "REFLATION", "GLOBAL": "STAGFLATION"},
        region_velocity_signals={"US": 0.6, "EM": 0.5, "GLOBAL": 0.4},
        market_event=event,
    )
    active = [symbol for symbol, plan in plans.items() if plan.engine == "ENGINE_B" and plan.core_target_weight != 0.0]
    assert len(active) == 2
    assert strategy._last_engine_b_extreme_active is True


def test_barbell_macro_extreme_regime_uses_higher_phase_three_multiplier(tmp_path):
    security_master_path = tmp_path / "extreme_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
        enable_extreme_concentration_layer=True,
    )
    strategy._bar_index = 40
    strategy._engine_b_state["SPY"] = {
        "sign": 1,
        "peak_score": 2.0,
        "peak_velocity": 0.7,
        "entry_bar": 0,
        "entry_price": 100.0,
        "phase": 2,
    }
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.40,
        score=2.0,
        region="US",
        theme="REFLATION",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.40,
        coherence=0.8,
        velocity_signal=0.6,
        extreme_regime=True,
    )
    bar = Bar("SPY", datetime(2020, 4, 10), 116.0, 117.0, 115.0, 116.0, 1_000_000.0, 116.0)

    phase, weight_mult, _ = strategy._core_phase_definition(symbol="SPY", plan=plan, bar=bar, atr=4.0)
    assert phase == 3
    assert weight_mult == pytest.approx(1.5)


def test_barbell_macro_convex_proxy_adds_small_extreme_dislocation_sleeve(tmp_path):
    security_master = _write_security_master(tmp_path / "convex_proxy_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_convex_dislocation_layer=True,
    )
    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.40,
        score=2.2,
        region="US",
        theme="CRISIS",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.40,
        coherence=0.8,
        core_phase=3,
        extreme_regime=True,
    )

    out = strategy._apply_convex_dislocation_sleeve(plans={"SPY": plan})

    assert out["SPY"].convex_target_weight > 0.0
    assert out["SPY"].target_weight == pytest.approx(
        out["SPY"].core_target_weight + out["SPY"].overlay_target_weight + out["SPY"].convex_target_weight
    )
    assert out["SPY"].convex_reason == "ENGINE_B_CONVEX_PROXY"


def test_barbell_macro_crisis_trend_layer_adds_price_only_trend(tmp_path):
    security_master = _write_security_master(tmp_path / "crisis_trend_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_crisis_trend_layer=True,
    )
    for idx in range(140):
        strategy._closes["SPY"].append(100.0 + idx * 0.5 + (idx % 2) * 0.1)
    strategy._last_engine_b_feedback["a_stress"] = True
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="MIXED",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_DORMANT",
        )
    }

    out = strategy._apply_crisis_trend_sleeve(plans=plans)

    assert out["SPY"].crisis_trend_target_weight > 0.0
    assert out["SPY"].crisis_trend_reason == "CRISIS_TREND"


def test_barbell_macro_dollar_squeeze_layer_adds_usd_and_short_em(tmp_path):
    security_master_path = tmp_path / "dollar_squeeze_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "UUP,2007-02-20,,FX,US Dollar Basket,US,FX",
                "EEM,2003-04-14,,Market,Emerging Markets Equity,EM,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["UUP", "EEM"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("UUP", "EEM"),
        enable_dollar_squeeze_layer=True,
    )
    for idx in range(80):
        strategy._closes["UUP"].append(25.0 + idx * 0.08 + (idx % 3) * 0.01)
        strategy._closes["EEM"].append(45.0 - idx * 0.05 + (idx % 2) * 0.02)
    strategy._last_engine_b_feedback["a_stress"] = True
    plans = {
        symbol: BarbellTargetPlan(
            symbol=symbol,
            engine="ENGINE_B",
            target_weight=0.0,
            score=0.0,
            region="US" if symbol == "UUP" else "EM",
            theme="MIXED",
            asset_class="FX" if symbol == "UUP" else "EQUITY_INDEX",
            reason="DISLOCATION_DORMANT",
        )
        for symbol in ("UUP", "EEM")
    }

    out = strategy._apply_dollar_squeeze_sleeve(plans=plans)

    assert out["UUP"].dollar_squeeze_target_weight > 0.0
    assert out["EEM"].dollar_squeeze_target_weight < 0.0


def test_barbell_macro_liquidation_reversal_layer_adds_confirmed_washout_long(tmp_path):
    security_master = _write_security_master(tmp_path / "reversal_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_liquidation_reversal_layer=True,
    )
    closes = [
        110.0,
        109.0,
        108.0,
        107.0,
        106.0,
        105.0,
        104.0,
        103.0,
        102.0,
        101.0,
        100.0,
        98.0,
        96.0,
        94.0,
        92.0,
        90.0,
        88.0,
        86.0,
        84.0,
        82.0,
        80.0,
        81.0,
        82.0,
        83.0,
        84.0,
        86.0,
    ]
    for close in closes:
        strategy._closes["SPY"].append(close)
    plans = {
        "SPY": BarbellTargetPlan(
            symbol="SPY",
            engine="ENGINE_B",
            target_weight=0.0,
            score=0.0,
            region="US",
            theme="MIXED",
            asset_class="EQUITY_INDEX",
            reason="DISLOCATION_DORMANT",
        )
    }

    out = strategy._apply_liquidation_reversal_sleeve(plans=plans)

    assert out["SPY"].reversal_target_weight > 0.0
    assert out["SPY"].reversal_reason == "LIQUIDATION_REVERSAL"


def test_portfolio_closed_trade_captures_barbell_trade_metadata():
    portfolio = Portfolio(initial_cash=100_000.0)
    entry_fill = FillEvent(
        timestamp=datetime(2020, 1, 2),
        symbol="SPY",
        side=Side.BUY,
        quantity=100,
        price=100.0,
        commission=0.0,
        slippage_cost=0.0,
        order_id="entry",
        metadata={
            "strategy": "GLOBAL_MACRO_BARBELL",
            "engine": "ENGINE_B",
            "reason": "ENGINE_B_ENTRY",
            "region": "US",
            "theme": "REFLATION",
            "asset_class": "EQUITY_INDEX",
            "target_weight": 0.40,
            "score": 1.5,
            "coherence": 0.8,
            "velocity_signal": 0.4,
            "core_phase": 1,
            "overlay_phase": 0,
            "reallocation_target_weight": 0.0,
            "extreme_regime": False,
        },
    )
    add_fill = FillEvent(
        timestamp=datetime(2020, 1, 20),
        symbol="SPY",
        side=Side.BUY,
        quantity=50,
        price=104.0,
        commission=0.0,
        slippage_cost=0.0,
        order_id="add",
        metadata={
            "strategy": "GLOBAL_MACRO_BARBELL",
            "engine": "ENGINE_B",
            "reason": "ENGINE_B_ENTRY",
            "region": "US",
            "theme": "REFLATION",
            "asset_class": "EQUITY_INDEX",
            "target_weight": 0.65,
            "score": 2.1,
            "coherence": 0.9,
            "velocity_signal": 0.7,
            "core_phase": 3,
            "overlay_phase": 0,
            "reallocation_target_weight": 0.15,
            "extreme_regime": True,
        },
    )
    exit_fill = FillEvent(
        timestamp=datetime(2020, 2, 10),
        symbol="SPY",
        side=Side.SELL,
        quantity=150,
        price=110.0,
        commission=0.0,
        slippage_cost=0.0,
        order_id="exit",
        metadata={
            "strategy": "GLOBAL_MACRO_BARBELL",
            "engine": "ENGINE_B",
            "reason": "ENGINE_B_EXIT",
            "region": "US",
            "theme": "REFLATION",
            "asset_class": "EQUITY_INDEX",
            "target_weight": 0.0,
            "score": 1.0,
            "coherence": 0.6,
            "velocity_signal": 0.2,
            "core_phase": 3,
            "overlay_phase": 0,
            "reallocation_target_weight": 0.0,
            "extreme_regime": True,
        },
    )

    portfolio.on_fill(entry_fill)
    portfolio.on_fill(add_fill)
    portfolio.on_fill(exit_fill)

    assert len(portfolio.closed_trades) == 1
    trade = portfolio.closed_trades[0]
    assert trade.entry_timestamp == datetime(2020, 1, 2)
    assert trade.exit_timestamp == datetime(2020, 2, 10)
    assert trade.direction == 1
    assert trade.metadata["engine"] == "ENGINE_B"
    assert trade.metadata["entry_reason"] == "ENGINE_B_ENTRY"
    assert trade.metadata["exit_reason"] == "ENGINE_B_EXIT"
    assert trade.metadata["max_core_phase"] == 3
    assert trade.metadata["reallocation_seen"] is True
    assert trade.metadata["extreme_regime_seen"] is True
    assert trade.metadata["days_held"] == 39


def test_barbell_trade_context_sync_captures_daily_phase_and_extreme_regime_state(tmp_path):
    security_master = _write_security_master(tmp_path / "sync_security_master.csv")
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
        enable_phase_stop_layer=True,
        enable_engine_a_reallocation_layer=True,
        enable_extreme_concentration_layer=True,
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 4, 1, 9, 30),
            symbol="SPY",
            side=Side.BUY,
            quantity=100,
            price=100.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="entry",
            metadata={
                "strategy": "GLOBAL_MACRO_BARBELL",
                "engine": "ENGINE_B",
                "reason": "ENGINE_B_ENTRY",
                "region": "US",
                "theme": "REFLATION",
                "asset_class": "EQUITY_INDEX",
                "target_weight": 0.40,
                "score": 1.2,
                "coherence": 0.5,
                "velocity_signal": 0.2,
                "core_phase": 1,
                "overlay_phase": 0,
                "reallocation_target_weight": 0.0,
                "extreme_regime": False,
            },
        )
    )

    plan = BarbellTargetPlan(
        symbol="SPY",
        engine="ENGINE_B",
        target_weight=0.60,
        score=2.3,
        region="US",
        theme="CRISIS",
        asset_class="EQUITY_INDEX",
        reason="DISLOCATION_THEME",
        core_target_weight=0.45,
        coherence=0.9,
        velocity_signal=0.8,
        core_phase=3,
        overlay_phase=0,
        reallocation_target_weight=0.15,
        extreme_regime=True,
    )
    market_event = MarketEvent(
        timestamp=datetime(2020, 4, 20, 16, 0),
        bars={"SPY": Bar("SPY", datetime(2020, 4, 20, 16, 0), 110.0, 111.0, 109.0, 110.0, 1_000_000.0, 110.0)},
    )
    strategy._sync_barbell_trade_context(
        market_event=market_event,
        plans={"SPY": plan},
        projected_positions={"SPY": 120},
        prices={"SPY": 110.0},
        equity=100_000.0,
    )
    strategy._sync_barbell_trade_context(
        market_event=market_event,
        plans={"SPY": plan},
        projected_positions={"SPY": 120},
        prices={"SPY": 110.0},
        equity=100_000.0,
    )

    portfolio.on_fill(
        FillEvent(
            timestamp=datetime(2020, 4, 21, 9, 30),
            symbol="SPY",
            side=Side.SELL,
            quantity=100,
            price=111.0,
            commission=0.0,
            slippage_cost=0.0,
            order_id="exit",
            metadata={
                "strategy": "GLOBAL_MACRO_BARBELL",
                "engine": "ENGINE_B",
                "reason": "ENGINE_B_EXIT",
                "region": "US",
                "theme": "CRISIS",
                "asset_class": "EQUITY_INDEX",
                "target_weight": 0.0,
                "score": 1.0,
                "coherence": 0.7,
                "velocity_signal": 0.3,
                "core_phase": 3,
                "overlay_phase": 0,
                "reallocation_target_weight": 0.0,
                "extreme_regime": True,
            },
        )
    )

    assert len(portfolio.closed_trades) == 1
    trade = portfolio.closed_trades[0]
    assert trade.metadata["max_core_phase"] == 3
    assert trade.metadata["phase_3_days"] == 1
    assert trade.metadata["extreme_regime_seen"] is True
    assert trade.metadata["extreme_regime_days"] == 1
    assert trade.metadata["reallocation_seen"] is True
    assert trade.metadata["reallocation_days"] == 1


def test_barbell_trade_attribution_summarizes_closed_trade_metadata(tmp_path):
    security_master_path = tmp_path / "attribution_security_master.csv"
    security_master_path.write_text(
        "\n".join(
            [
                "symbol,list_date,delist_date,sector,industry,region,asset_class",
                "SPY,1993-01-29,,Market,US Equity Index,US,Equity_Index",
            ]
        ),
        encoding="ascii",
    )
    security_master = CSVSecurityMasterDataSource(security_master_path)
    strategy = BarbellMacroWizardStrategy(
        symbols=["SPY"],
        macro_source=DummyMacroSource(rows={}),
        security_master=security_master,
        carry_symbols=(),
        dislocation_symbols=("SPY",),
    )
    portfolio = Portfolio(initial_cash=100_000.0)
    strategy.bind_portfolio(portfolio)
    portfolio.closed_trades.append(
        ClosedTrade(
            symbol="SPY",
            timestamp=datetime(2020, 3, 15),
            quantity=100.0,
            pnl=2_500.0,
            entry_timestamp=datetime(2020, 2, 20),
            exit_timestamp=datetime(2020, 3, 15),
            entry_price=100.0,
            exit_price=125.0,
            direction=1,
            metadata={
                "strategy": "GLOBAL_MACRO_BARBELL",
                "engine": "ENGINE_B",
                "entry_reason": "ENGINE_B_ENTRY",
                "exit_reason": "ENGINE_B_EXIT",
                "entry_region": "US",
                "entry_theme": "DISINFLATION",
                "entry_asset_class": "EQUITY_INDEX",
                "entry_core_phase": 1,
                "entry_overlay_phase": 0,
                "max_core_phase": 3,
                "max_overlay_phase": 0,
                "max_abs_target_weight": 0.60,
                "max_abs_score": 2.0,
                "max_coherence": 0.85,
                "max_velocity_signal": 0.5,
                "extreme_regime_seen": True,
                "reallocation_seen": True,
                "phase_1_days": 4,
                "phase_2_days": 8,
                "phase_3_days": 12,
                "overlay_phase_1_days": 0,
                "overlay_phase_2_days": 0,
                "overlay_phase_3_days": 0,
                "extreme_regime_days": 12,
                "reallocation_days": 6,
                "direction_label": "LONG",
                "days_held": 24,
            },
        )
    )

    attribution = strategy.trade_attribution()
    assert attribution["closed_trade_count"] == 1
    assert attribution["by_engine"]["ENGINE_B"]["net_pnl"] == pytest.approx(2500.0)
    assert attribution["by_symbol"]["SPY"]["closed_trades"] == pytest.approx(1.0)
    assert attribution["by_max_core_phase"]["3"]["net_pnl"] == pytest.approx(2500.0)
    assert attribution["by_episode_exit"]["shock_2020"]["net_pnl"] == pytest.approx(2500.0)
    assert attribution["by_phase3_participation"]["True"]["net_pnl"] == pytest.approx(2500.0)
    assert attribution["rows"][0]["phase_3_days"] == 12
