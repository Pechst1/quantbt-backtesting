from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import FillEvent, MarketEvent, OrderEvent
from quantbt.core.models import Bar

if TYPE_CHECKING:  # pragma: no cover
    from quantbt.altdata.base import BorrowDataSource


@dataclass(slots=True)
class ExecutionConfig:
    slippage_bps: float = 2.0
    spread_bps: float = 5.0
    volume_impact_bps: float = 0.0
    commission_fixed: float = 0.0
    commission_pct: float = 0.0005
    sec_fee_rate: float = 0.000008
    exchange_fee_per_share: float = 0.0002
    reject_hard_to_borrow: bool = True
    reject_when_no_borrow_data: bool = False


class SimulatedExecutionHandler:
    def __init__(
        self,
        config: ExecutionConfig | None = None,
        borrow_source: BorrowDataSource | None = None,
        symbol_multipliers: dict[str, float] | None = None,
        futures_symbols: set[str] | None = None,
    ) -> None:
        self.config = config or ExecutionConfig()
        self.borrow_source = borrow_source
        self.symbol_multipliers = {
            symbol.upper(): max(float(multiplier), 1e-9)
            for symbol, multiplier in (symbol_multipliers or {}).items()
        }
        self.futures_symbols = {symbol.upper() for symbol in (futures_symbols or set())}
        self._pending_orders: list[OrderEvent] = []
        self.rejected_orders: int = 0

    def _multiplier(self, symbol: str) -> float:
        return float(self.symbol_multipliers.get(symbol.upper(), 1.0))

    def submit_order(self, order: OrderEvent) -> None:
        self._pending_orders.append(order)

    def _match_order(self, order: OrderEvent, bar: Bar) -> float | None:
        if order.order_type in (OrderType.MARKET, OrderType.MOO):
            return bar.open
        if order.order_type == OrderType.MOC:
            return bar.close

        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                return None
            if order.side == Side.BUY and bar.low <= order.limit_price:
                return bar.open if bar.open <= order.limit_price else order.limit_price
            if order.side == Side.SELL and bar.high >= order.limit_price:
                return bar.open if bar.open >= order.limit_price else order.limit_price
            return None

        if order.order_type == OrderType.STOP_LOSS:
            stop = order.stop_price if order.stop_price is not None else order.stop_loss
            if stop is None:
                return None
            if order.side == Side.BUY and bar.high >= stop:
                return bar.open if bar.open >= stop else stop
            if order.side == Side.SELL and bar.low <= stop:
                return bar.open if bar.open <= stop else stop
            return None

        if order.order_type == OrderType.TAKE_PROFIT:
            target = order.limit_price
            if target is None:
                target = order.take_profit
            if target is None:
                return None
            if order.side == Side.BUY and bar.low <= target:
                return bar.open if bar.open <= target else target
            if order.side == Side.SELL and bar.high >= target:
                return bar.open if bar.open >= target else target
            return None

        return None

    def _apply_cost_model(self, side: Side, raw_price: float, quantity: int, bar: Bar) -> tuple[float, float]:
        half_spread = raw_price * (self.config.spread_bps / 10_000.0) / 2.0
        participation = abs(quantity) / max(bar.volume, 1.0)
        impact_bps = self.config.volume_impact_bps * min(math.sqrt(participation), 5.0)
        slippage_bps = self.config.slippage_bps + impact_bps

        if side == Side.BUY:
            spread_price = raw_price + half_spread
            filled = spread_price * (1.0 + slippage_bps / 10_000.0)
        else:
            spread_price = raw_price - half_spread
            filled = spread_price * (1.0 - slippage_bps / 10_000.0)

        slippage_cost = abs(filled - raw_price)
        return max(filled, 1e-9), slippage_cost

    def _commission(self, quantity: int, price: float, side: Side, symbol: str) -> float:
        notional = abs(quantity * price * self._multiplier(symbol))
        commission = self.config.commission_fixed
        commission += notional * self.config.commission_pct
        commission += abs(quantity) * self.config.exchange_fee_per_share
        if side == Side.SELL and symbol.upper() not in self.futures_symbols:
            commission += notional * self.config.sec_fee_rate
        return commission

    def _borrow_snapshot(self, symbol: str, as_of: date):
        if self.borrow_source is None:
            return None
        return self.borrow_source.snapshot(symbol, as_of)

    def _is_short_restricted(self, order: OrderEvent, event: MarketEvent) -> bool:
        if not bool(order.metadata.get("short_sale", False)):
            return False
        snapshot = self._borrow_snapshot(order.symbol, event.timestamp.date())
        if snapshot is None:
            return self.config.reject_when_no_borrow_data
        if self.config.reject_hard_to_borrow and bool(snapshot.is_hard_to_borrow):
            return True
        return False

    def _create_protective_orders(self, order: OrderEvent, timestamp) -> list[OrderEvent]:
        if order.metadata.get("is_protective"):
            return []

        out: list[OrderEvent] = []
        protective_side = Side.SELL if order.side == Side.BUY else Side.BUY
        oco_group = uuid4().hex
        if order.take_profit is not None:
            out.append(
                OrderEvent(
                    timestamp=timestamp,
                    symbol=order.symbol,
                    side=protective_side,
                    quantity=order.quantity,
                    order_type=OrderType.TAKE_PROFIT,
                    limit_price=order.take_profit,
                    metadata={"is_protective": True, "parent_order_id": order.order_id},
                    oco_group=oco_group,
                )
            )
        if order.stop_loss is not None:
            out.append(
                OrderEvent(
                    timestamp=timestamp,
                    symbol=order.symbol,
                    side=protective_side,
                    quantity=order.quantity,
                    order_type=OrderType.STOP_LOSS,
                    stop_price=order.stop_loss,
                    metadata={"is_protective": True, "parent_order_id": order.order_id},
                    oco_group=oco_group,
                )
            )
        return out

    def _attempt_fill(self, order: OrderEvent, event: MarketEvent) -> FillEvent | str | None:
        bar = event.bars.get(order.symbol)
        if bar is None or bar.is_stale:
            # A forward-filled bar is not a real trading session: keep the order pending.
            return None
        if self._is_short_restricted(order, event):
            return "REJECTED_SHORT"

        raw_price = self._match_order(order, bar)
        if raw_price is None:
            return None

        filled_price, slippage_cost = self._apply_cost_model(order.side, raw_price, order.quantity, bar)
        multiplier = self._multiplier(order.symbol)
        commission = self._commission(order.quantity, filled_price, order.side, order.symbol)

        metadata = dict(order.metadata)
        if bool(order.metadata.get("short_sale", False)):
            snapshot = self._borrow_snapshot(order.symbol, event.timestamp.date())
            if snapshot is not None:
                metadata["borrow_rate"] = float(snapshot.annualized_fee)
                metadata["is_hard_to_borrow"] = bool(snapshot.is_hard_to_borrow)

        return FillEvent(
            timestamp=event.timestamp,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=filled_price,
            commission=commission,
            slippage_cost=slippage_cost * order.quantity * multiplier,
            order_id=order.order_id,
            metadata=metadata,
        )

    def execute_immediate(self, order: OrderEvent, event: MarketEvent) -> FillEvent | None:
        outcome = self._attempt_fill(order, event)
        if outcome is None:
            return None
        if isinstance(outcome, str):
            self.rejected_orders += 1
            return None
        self._pending_orders.extend(self._create_protective_orders(order, event.timestamp))
        return outcome

    def _fill_priority(self, order: OrderEvent, event: MarketEvent) -> int:
        """Order in which pending orders are resolved inside one bar.

        Daily bars do not say whether the high or the low came first, so resolution is
        pessimistic: anything that executes at the open goes first, then stops, then
        limits/targets, and market-on-close orders last.
        """
        if order.order_type in (OrderType.MARKET, OrderType.MOO):
            return 0
        if order.order_type == OrderType.MOC:
            return 3
        bar = event.bars.get(order.symbol)
        if bar is not None and self._match_order(order, bar) == bar.open:
            return 0
        if order.order_type == OrderType.STOP_LOSS:
            return 1
        return 2

    def _reconcile_protective(self, order: OrderEvent, positions: dict[str, float]) -> OrderEvent | None:
        """Drop or shrink a protective order so it can only reduce the current position."""
        if not order.metadata.get("is_protective"):
            return order
        current = float(positions.get(order.symbol, 0.0))
        reduces = (current > 0 and order.side == Side.SELL) or (current < 0 and order.side == Side.BUY)
        if not reduces:
            return None
        max_qty = int(abs(current))
        if max_qty <= 0:
            return None
        if order.quantity > max_qty:
            order.quantity = max_qty
        return order

    def cancel_orphaned_protective_orders(self, positions: dict[str, float]) -> int:
        """Cancel stop/target orders whose position was closed or reversed elsewhere."""
        kept: list[OrderEvent] = []
        cancelled = 0
        for order in self._pending_orders:
            if self._reconcile_protective(order, positions) is None:
                cancelled += 1
                continue
            kept.append(order)
        self._pending_orders = kept
        return cancelled

    def on_market(self, event: MarketEvent, positions: dict[str, float] | None = None) -> list[FillEvent]:
        """Match pending orders against the new bar.

        ``positions`` maps symbol to signed quantity before this bar. When given, protective
        orders are reconciled against the running position so a stop or target can never
        open a new position after the strategy has already exited.
        """
        fills: list[FillEvent] = []
        remaining: list[OrderEvent] = []
        filled_oco_groups: set[str] = set()
        running = None if positions is None else {k.upper(): float(v) for k, v in positions.items()}

        queue = sorted(self._pending_orders, key=lambda order: self._fill_priority(order, event))
        while queue:
            order = queue.pop(0)
            if order.oco_group and order.oco_group in filled_oco_groups:
                continue
            if running is not None and self._reconcile_protective(order, running) is None:
                continue
            outcome = self._attempt_fill(order, event)
            if outcome is None:
                remaining.append(order)
                continue
            if isinstance(outcome, str):
                self.rejected_orders += 1
                continue

            fills.append(outcome)
            if running is not None:
                signed = outcome.quantity if outcome.side == Side.BUY else -outcome.quantity
                running[order.symbol] = running.get(order.symbol, 0.0) + signed
            if order.oco_group:
                filled_oco_groups.add(order.oco_group)

            protective = self._create_protective_orders(order, event.timestamp)
            if protective and order.order_type in (OrderType.MARKET, OrderType.MOO):
                # The entry filled at the open, so the rest of this bar can already hit its
                # stop or target. Resolve them against the same bar (stop first).
                protective.sort(key=lambda child: self._fill_priority(child, event))
                queue = protective + queue
            else:
                remaining.extend(protective)

        self._pending_orders = [
            order for order in remaining if not (order.oco_group and order.oco_group in filled_oco_groups)
        ]
        return fills

    @property
    def pending_orders(self) -> int:
        return len(self._pending_orders)
