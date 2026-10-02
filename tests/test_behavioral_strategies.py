from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from quantbt.altdata.base import (
    CorporateActionsDataSource,
    EarningsDataSource,
    FactorDataSource,
    FundamentalsDataSource,
)
from quantbt.altdata.models import (
    BorrowSnapshot,
    CorporateAction,
    EarningsAnnouncement,
    FundamentalSnapshot,
)
from quantbt.core.enums import OrderType, Side
from quantbt.core.events import MarketEvent
from quantbt.core.models import Bar
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.behavioral import (
    AntiLotteryShortStrategy,
    CapitulationBuyStrategy,
    PEADInconsistencyAvoidanceStrategy,
)


class StubEarningsSource(EarningsDataSource):
    def __init__(self, events: list[EarningsAnnouncement]) -> None:
        self.events = events

    def announcements_on(self, as_of: date) -> list[EarningsAnnouncement]:
        return [event for event in self.events if event.announcement_ts.date() == as_of]

    def next_announcement_after(self, symbol: str, as_of: date) -> datetime | None:
        future = [
            event.announcement_ts
            for event in self.events
            if event.symbol == symbol and event.announcement_ts.date() > as_of
        ]
        return min(future) if future else None


class StubFundamentalsSource(FundamentalsDataSource):
    def __init__(self, net_income_map: dict[str, float]) -> None:
        self.net_income_map = {symbol.upper(): value for symbol, value in net_income_map.items()}

    def snapshot(self, symbol: str, as_of: date) -> FundamentalSnapshot | None:
        symbol = symbol.upper()
        if symbol not in self.net_income_map:
            return None
        return FundamentalSnapshot(
            symbol=symbol,
            as_of=datetime(as_of.year, as_of.month, as_of.day),
            net_income_ttm=self.net_income_map[symbol],
            shares_outstanding=1_000_000.0,
        )


class StubFactorSource(FactorDataSource):
    def factor_window(self, dates: pd.Index) -> pd.DataFrame:
        idx = pd.to_datetime(dates)
        return pd.DataFrame(
            {
                "market_excess": 0.0,
                "smb": 0.0,
                "hml": 0.0,
                "rf": 0.0,
            },
            index=idx,
        )


class StubActionsSource(CorporateActionsDataSource):
    def __init__(self, actions: dict[tuple[str, date], list[CorporateAction]] | None = None) -> None:
        self.actions = actions or {}

    def actions_on(self, symbol: str, as_of: date) -> list[CorporateAction]:
        return list(self.actions.get((symbol.upper(), as_of), []))


def _market_event(ts: datetime, symbol_to_close: dict[str, float], volume: float = 1000.0) -> MarketEvent:
    bars = {}
    for symbol, close in symbol_to_close.items():
        bars[symbol] = Bar(
            symbol=symbol,
            timestamp=ts,
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=volume,
            adj_close=close,
        )
    return MarketEvent(timestamp=ts, bars=bars)


def test_pead_strategy_generates_announcement_day_moo_signal():
    events = [
        EarningsAnnouncement(
            symbol="AAA",
            announcement_ts=datetime(2024, 1, 5, 16, 0),
            eps_estimate=1.00,
            eps_actual=1.50,
            estimate_std=0.20,
        )
    ]
    strategy = PEADInconsistencyAvoidanceStrategy(
        symbols=["AAA"],
        earnings_source=StubEarningsSource(events),
        sue_threshold=2.0,
        hold_days=60,
    )
    strategy.bind_portfolio(Portfolio(initial_cash=100_000.0))

    day_announcement = _market_event(datetime(2024, 1, 5), {"AAA": 100.0})
    signals_announcement = strategy.on_data(day_announcement)

    assert len(signals_announcement) == 1
    assert signals_announcement[0].order_type == OrderType.MOO
    assert signals_announcement[0].side == Side.BUY


def test_anti_lottery_rebalances_into_high_idio_vol_short():
    symbols = ["AAA", "BBB"]
    strategy = AntiLotteryShortStrategy(
        symbols=symbols,
        fundamentals_source=StubFundamentalsSource({"AAA": -10.0, "BBB": -5.0}),
        factor_source=StubFactorSource(),
        rebalance_top_pct=0.5,
        lookback_days=10,
        min_price=5.0,
        stop_loss_pct=0.2,
    )
    strategy.bind_portfolio(Portfolio(initial_cash=100_000.0))

    dates = pd.date_range("2024-01-01", "2024-02-03", freq="D")
    price_a = 20.0
    price_b = 20.0
    emitted_sells = []
    for i, ts in enumerate(dates):
        # AAA has large alternating swings -> higher idiosyncratic volatility.
        price_a *= 1.05 if i % 2 == 0 else 0.95
        price_b *= 1.001
        event = _market_event(ts.to_pydatetime(), {"AAA": price_a, "BBB": price_b})
        signals = strategy.on_data(event)
        emitted_sells.extend([s for s in signals if s.side == Side.SELL])

    assert emitted_sells, "Expected at least one short-entry signal at monthly rebalance."
    assert any(signal.metadata.get("short_sale") for signal in emitted_sells)


def test_capitulation_buy_fires_on_panic_pattern():
    strategy = CapitulationBuyStrategy(
        symbols=["AAA"],
        corporate_actions_source=StubActionsSource(),
        drop_threshold=-0.15,
        volume_multiplier=3.0,
        rsi_entry=20.0,
        rsi_exit=50.0,
        max_holding_days=10,
    )
    strategy.bind_portfolio(Portfolio(initial_cash=100_000.0))

    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    closes = [100.0] * 33 + [95.0, 90.0, 84.0] + [85.0, 86.0, 87.0, 88.0]
    signals_seen = []
    for i, ts in enumerate(dates):
        vol = 1000.0
        if i == 35:
            vol = 5000.0
        event = _market_event(ts.to_pydatetime(), {"AAA": closes[i]}, volume=vol)
        signals_seen.extend(strategy.on_data(event))

    buy_signals = [signal for signal in signals_seen if signal.side == Side.BUY]
    assert buy_signals, "Expected capitulation buy signal when panic conditions align."
    assert buy_signals[0].order_type == OrderType.MOC
