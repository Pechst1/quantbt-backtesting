from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from quantbt.analytics.performance import PerformanceReport, build_performance_report
from quantbt.analytics.reporting import TearSheetReporter
from quantbt.core.enums import EventType, OrderType, Side
from quantbt.core.events import FillEvent, MarketEvent, OrderEvent, SignalEvent
from quantbt.data.handler import PublicOHLCVDataHandler
from quantbt.execution.handler import SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategy.base import BaseStrategy

if TYPE_CHECKING:  # pragma: no cover
    from quantbt.altdata.universe import PointInTimeIndexUniverse


@dataclass(slots=True)
class BacktestResult:
    report: PerformanceReport
    artifacts: dict[str, str] = field(default_factory=dict)
    submitted_orders: int = 0
    fills: int = 0
    margin_calls: int = 0
    rejected_orders: int = 0
    risk_rejections: int = 0
    universe_rejections: int = 0
    borrow_fees_paid: float = 0.0


class BacktestEngine:
    def __init__(
        self,
        *,
        data_handler: PublicOHLCVDataHandler,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        execution_handler: SimulatedExecutionHandler,
        reporter: TearSheetReporter | None = None,
        universe: PointInTimeIndexUniverse | None = None,
    ) -> None:
        self.data_handler = data_handler
        self.strategy = strategy
        self.portfolio = portfolio
        self.execution_handler = execution_handler
        self.reporter = reporter
        self.universe = universe
        self._queue: deque[MarketEvent | SignalEvent | OrderEvent | FillEvent] = deque()

        self.strategy.bind_portfolio(self.portfolio)

        self._submitted_orders = 0
        self._fills = 0
        self._margin_calls = 0
        self._rejected_orders = 0
        self._universe_rejections = 0
        self._current_market_event: MarketEvent | None = None
        self._last_timestamp: datetime | None = None

    def _passes_universe_rule(self, signal_event: SignalEvent, order_event: OrderEvent | None) -> bool:
        if self.universe is None or order_event is None:
            return True

        as_of = signal_event.timestamp.date()
        symbol = signal_event.symbol.upper()
        in_universe = self.universe.is_member(symbol, as_of)
        if in_universe:
            return True

        # Outside universe: allow reducing/closing an existing position, block fresh exposure.
        position = self.portfolio.position_for_symbol(symbol)
        old_qty = float(position.quantity)
        signed = float(order_event.quantity if order_event.side == Side.BUY else -order_event.quantity)
        new_qty = old_qty + signed
        if abs(new_qty) <= abs(old_qty) + 1e-9:
            return True
        self._universe_rejections += 1
        return False

    def _handle_market_event(self, market_event: MarketEvent) -> None:
        self._current_market_event = market_event

        fills = self.execution_handler.on_market(market_event)
        for fill in fills:
            self.portfolio.on_fill(fill)
            self._fills += 1

        self.portfolio.apply_borrow_fees(
            timestamp=market_event.timestamp,
            bars=market_event.bars,
            borrow_source=self.execution_handler.borrow_source,
            previous_timestamp=self._last_timestamp,
        )

        snapshot = self.portfolio.mark_to_market(market_event.timestamp, market_event.bars)
        if snapshot.margin_call:
            self._margin_calls += 1
            for order in self.portfolio.generate_margin_liquidation_orders(market_event.timestamp):
                self._queue.append(order)

        for order in self.portfolio.generate_hard_stop_orders(market_event.timestamp, market_event.bars):
            self._queue.append(order)

        signals = self.strategy.on_data(market_event)
        for signal in signals:
            self._queue.append(signal)

        self._last_timestamp = market_event.timestamp

    def _handle_signal_event(self, signal_event: SignalEvent) -> None:
        latest_bar = self.data_handler.get_latest_bar(signal_event.symbol)
        order = self.portfolio.create_order_from_signal(signal_event, latest_bar)
        if order is not None and self._passes_universe_rule(signal_event, order):
            self._queue.append(order)

    def _handle_order_event(self, order_event: OrderEvent) -> None:
        self._submitted_orders += 1
        if (
            self._current_market_event is not None
            and order_event.order_type == OrderType.MOC
            and order_event.timestamp == self._current_market_event.timestamp
        ):
            fill = self.execution_handler.execute_immediate(order_event, self._current_market_event)
            if fill is not None:
                self.portfolio.on_fill(fill)
                self._fills += 1
            return
        self.execution_handler.submit_order(order_event)

    def _handle_fill_event(self, fill_event: FillEvent) -> None:
        self.portfolio.on_fill(fill_event)
        self._fills += 1

    def _drain_queue(self) -> None:
        while self._queue:
            event = self._queue.popleft()
            if event.event_type == EventType.MARKET:
                self._handle_market_event(event)  # type: ignore[arg-type]
            elif event.event_type == EventType.SIGNAL:
                self._handle_signal_event(event)  # type: ignore[arg-type]
            elif event.event_type == EventType.ORDER:
                self._handle_order_event(event)  # type: ignore[arg-type]
            elif event.event_type == EventType.FILL:
                self._handle_fill_event(event)  # type: ignore[arg-type]

    def run(self, run_name: str = "backtest") -> BacktestResult:
        while self.data_handler.has_next():
            self._queue.append(self.data_handler.stream_next())
            self._drain_queue()

        report = build_performance_report(
            snapshots=self.portfolio.history,
            closed_trades=self.portfolio.closed_trades,
            interval=self.data_handler.interval,
        )
        artifacts = self.reporter.generate(report, run_name=run_name) if self.reporter else {}

        self._rejected_orders = self.execution_handler.rejected_orders
        return BacktestResult(
            report=report,
            artifacts=artifacts,
            submitted_orders=self._submitted_orders,
            fills=self._fills,
            margin_calls=self._margin_calls,
            rejected_orders=self._rejected_orders,
            risk_rejections=self.portfolio.rejected_by_risk,
            universe_rejections=self._universe_rejections,
            borrow_fees_paid=self.portfolio.borrow_fees_paid,
        )
