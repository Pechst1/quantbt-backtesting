from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from math import isclose
from typing import TYPE_CHECKING, Any

import numpy as np

from quantbt.core.enums import OrderType, Side
from quantbt.core.events import FillEvent, OrderEvent, SignalEvent
from quantbt.core.models import Bar, ClosedTrade, Position
from quantbt.portfolio.sizing import BasePositionSizer, FixedFractionalSizer

if TYPE_CHECKING:  # pragma: no cover
    from quantbt.altdata.base import BorrowDataSource

# An annualized rate, either constant or looked up per date (e.g. from a T-bill series).
RateInput = float | Callable[[date], float]


@dataclass(slots=True)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    equity: float
    gross_exposure: float
    net_exposure: float
    used_margin: float
    margin_ratio: float
    unrealized_pnl: float
    realized_pnl: float
    borrow_fees_paid: float
    margin_call: bool


class Portfolio:
    def __init__(
        self,
        initial_cash: float = 100_000.0,
        leverage: float = 2.0,
        maintenance_margin_ratio: float = 0.25,
        position_sizer: BasePositionSizer | None = None,
        max_gross_exposure: float | None = None,
        max_net_exposure: float | None = None,
        max_sector_exposure: float | None = None,
        symbol_sectors: dict[str, str] | None = None,
        symbol_multipliers: dict[str, float] | None = None,
        futures_symbols: set[str] | None = None,
        hard_stop_loss_pct: float | None = None,
        cash_interest_rate: RateInput = 0.0,
        margin_interest_rate: RateInput = 0.05,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.latest_equity = float(initial_cash)
        self.leverage = max(leverage, 1.0)
        self.maintenance_margin_ratio = max(maintenance_margin_ratio, 0.0)
        self.position_sizer = position_sizer or FixedFractionalSizer()

        self.max_gross_exposure = max_gross_exposure
        self.max_net_exposure = max_net_exposure
        self.max_sector_exposure = max_sector_exposure
        self.symbol_sectors = {symbol.upper(): sector for symbol, sector in (symbol_sectors or {}).items()}
        self.symbol_multipliers = {
            symbol.upper(): max(float(multiplier), 1e-9)
            for symbol, multiplier in (symbol_multipliers or {}).items()
        }
        self.futures_symbols = {symbol.upper() for symbol in (futures_symbols or set())}
        self.hard_stop_loss_pct = hard_stop_loss_pct
        # Debit balances pay margin_interest_rate; free credit balances earn cash_interest_rate.
        self.cash_interest_rate = cash_interest_rate
        self.margin_interest_rate = margin_interest_rate

        self.positions: dict[str, Position] = {}
        self.history: list[PortfolioSnapshot] = []
        self.closed_trades: list[ClosedTrade] = []
        self.latest_bars: dict[str, Bar] = {}
        self.borrow_fees_paid: float = 0.0
        self.margin_interest_paid: float = 0.0
        self.cash_interest_earned: float = 0.0
        self.rejected_by_risk: int = 0
        self._risk_exit_pending: set[str] = set()
        self._open_trade_context: dict[str, dict[str, Any]] = {}

    def position_for_symbol(self, symbol: str) -> Position:
        symbol = symbol.upper()
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def _signed_quantity(self, side: Side, quantity: int) -> float:
        return float(quantity if side == Side.BUY else -quantity)

    def _multiplier(self, symbol: str) -> float:
        return float(self.symbol_multipliers.get(symbol.upper(), 1.0))

    def _is_futures_symbol(self, symbol: str) -> bool:
        return symbol.upper() in self.futures_symbols

    def _notional_value(self, symbol: str, quantity: float, price: float) -> float:
        return float(quantity) * float(price) * self._multiplier(symbol)

    def _unrealized_value(self, symbol: str, position: Position, price: float) -> float:
        if self._is_futures_symbol(symbol):
            return position.quantity * (price - position.avg_price) * self._multiplier(symbol)
        return position.quantity * price

    def _exposure_map(self) -> dict[str, float]:
        exposure: dict[str, float] = {}
        for symbol, position in self.positions.items():
            if isclose(position.quantity, 0.0):
                continue
            bar = self.latest_bars.get(symbol)
            if bar is not None:
                price = bar.close
            elif position.avg_price > 0:
                price = position.avg_price
            else:
                continue
            exposure[symbol] = self._notional_value(symbol, position.quantity, price)
        return exposure

    def _sector_exposure_ratios(self, exposure: dict[str, float], equity: float) -> dict[str, float]:
        if equity <= 0:
            return {}
        by_sector: dict[str, float] = {}
        for symbol, value in exposure.items():
            sector = self.symbol_sectors.get(symbol, "UNSPECIFIED")
            by_sector[sector] = by_sector.get(sector, 0.0) + abs(value)
        return {sector: gross / equity for sector, gross in by_sector.items()}

    def _worsens_limit(self, before: float, after: float, limit: float) -> bool:
        if abs(after) <= limit:
            return False
        return abs(after) > abs(before) + 1e-9

    def _passes_exposure_limits(self, symbol: str, side: Side, quantity: int, price: float) -> bool:
        if all(
            limit is None
            for limit in (self.max_gross_exposure, self.max_net_exposure, self.max_sector_exposure)
        ):
            return True

        equity = max(abs(self.latest_equity), 1e-9)
        exposure_before = self._exposure_map()
        exposure_after = dict(exposure_before)
        exposure_after[symbol] = exposure_after.get(symbol, 0.0) + self._notional_value(
            symbol,
            self._signed_quantity(side, quantity),
            price,
        )

        gross_before = sum(abs(value) for value in exposure_before.values()) / equity
        gross_after = sum(abs(value) for value in exposure_after.values()) / equity
        net_before = sum(exposure_before.values()) / equity
        net_after = sum(exposure_after.values()) / equity

        if self.max_gross_exposure is not None and self._worsens_limit(
            gross_before,
            gross_after,
            self.max_gross_exposure,
        ):
            return False
        if self.max_net_exposure is not None and self._worsens_limit(
            net_before,
            net_after,
            self.max_net_exposure,
        ):
            return False
        if self.max_sector_exposure is not None:
            before_sector = self._sector_exposure_ratios(exposure_before, equity)
            after_sector = self._sector_exposure_ratios(exposure_after, equity)
            for sector, ratio_after in after_sector.items():
                ratio_before = before_sector.get(sector, 0.0)
                if ratio_after > self.max_sector_exposure and ratio_after > ratio_before + 1e-9:
                    return False
        return True

    def create_order_from_signal(self, signal: SignalEvent, bar: Bar | None) -> OrderEvent | None:
        if bar is None:
            return None

        if signal.quantity is not None:
            quantity = int(signal.quantity)
        else:
            quantity = int(self.position_sizer.size(signal, self, bar))
        if quantity <= 0:
            return None

        metadata = dict(signal.metadata)
        current_position = self.position_for_symbol(signal.symbol)
        metadata.setdefault("short_sale", bool(signal.side == Side.SELL and current_position.quantity <= 0))

        if not self._passes_exposure_limits(signal.symbol.upper(), signal.side, quantity, bar.close):
            self.rejected_by_risk += 1
            return None

        return OrderEvent(
            timestamp=signal.timestamp,
            symbol=signal.symbol.upper(),
            side=signal.side,
            quantity=quantity,
            order_type=signal.order_type,
            limit_price=signal.limit_price,
            stop_price=signal.stop_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            metadata=metadata,
        )

    def _record_realized_trade(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        quantity: float,
        pnl: float,
        entry_timestamp: datetime | None = None,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        direction: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.closed_trades.append(
            ClosedTrade(
                symbol=symbol,
                timestamp=timestamp,
                quantity=quantity,
                pnl=pnl,
                entry_timestamp=entry_timestamp,
                exit_timestamp=timestamp,
                entry_price=entry_price,
                exit_price=exit_price,
                direction=direction,
                metadata=dict(metadata or {}),
            )
        )

    def _init_trade_context(
        self,
        *,
        symbol: str,
        fill: FillEvent,
        direction: int,
        entry_price: float,
    ) -> None:
        meta = dict(fill.metadata)
        self._open_trade_context[symbol] = {
            "entry_timestamp": fill.timestamp,
            "direction": direction,
            "strategy": meta.get("strategy"),
            "engine": meta.get("engine"),
            "entry_reason": meta.get("reason"),
            "entry_region": meta.get("region"),
            "entry_theme": meta.get("theme"),
            "entry_asset_class": meta.get("asset_class"),
            "entry_target_weight": float(meta.get("target_weight", 0.0) or 0.0),
            "entry_core_phase": int(meta.get("core_phase", 0) or 0),
            "entry_overlay_phase": int(meta.get("overlay_phase", 0) or 0),
            "entry_price_hint": float(entry_price),
            "max_core_phase": int(meta.get("core_phase", 0) or 0),
            "max_overlay_phase": int(meta.get("overlay_phase", 0) or 0),
            "max_abs_target_weight": abs(float(meta.get("target_weight", 0.0) or 0.0)),
            "max_abs_score": abs(float(meta.get("score", 0.0) or 0.0)),
            "max_coherence": abs(float(meta.get("coherence", 0.0) or 0.0)),
            "max_velocity_signal": abs(float(meta.get("velocity_signal", 0.0) or 0.0)),
            "extreme_regime_seen": bool(meta.get("extreme_regime", False)),
            "reallocation_seen": abs(float(meta.get("reallocation_target_weight", 0.0) or 0.0)) > 0.0,
            "phase_1_days": 0,
            "phase_2_days": 0,
            "phase_3_days": 0,
            "overlay_phase_1_days": 0,
            "overlay_phase_2_days": 0,
            "overlay_phase_3_days": 0,
            "extreme_regime_days": 0,
            "reallocation_days": 0,
            "last_observation_date": None,
            "last_reason": meta.get("reason"),
            "entry_metadata": meta,
            "last_metadata": meta,
        }

    def _merge_trade_context(
        self,
        *,
        symbol: str,
        fill: FillEvent,
        direction: int,
        position_avg_price: float,
    ) -> None:
        context = self._open_trade_context.get(symbol)
        if context is None or int(context.get("direction", 0)) != direction:
            self._init_trade_context(symbol=symbol, fill=fill, direction=direction, entry_price=position_avg_price)
            return
        self._merge_trade_context_metadata(
            symbol=symbol,
            metadata=fill.metadata,
            direction=direction,
            position_avg_price=position_avg_price,
        )

    def _merge_trade_context_metadata(
        self,
        *,
        symbol: str,
        metadata: dict[str, Any],
        direction: int,
        position_avg_price: float,
        timestamp: datetime | None = None,
        count_observation: bool = False,
    ) -> None:
        context = self._open_trade_context.get(symbol)
        if context is None or int(context.get("direction", 0)) != direction:
            synthetic_fill = FillEvent(
                timestamp=datetime.utcnow(),
                symbol=symbol,
                side=Side.BUY if direction > 0 else Side.SELL,
                quantity=0,
                price=position_avg_price,
                commission=0.0,
                slippage_cost=0.0,
                order_id="context-seed",
                metadata=dict(metadata),
            )
            self._init_trade_context(
                symbol=symbol,
                fill=synthetic_fill,
                direction=direction,
                entry_price=position_avg_price,
            )
            context = self._open_trade_context[symbol]
        meta = dict(metadata)
        context["max_core_phase"] = max(int(context.get("max_core_phase", 0)), int(meta.get("core_phase", 0) or 0))
        context["max_overlay_phase"] = max(
            int(context.get("max_overlay_phase", 0)),
            int(meta.get("overlay_phase", 0) or 0),
        )
        context["max_abs_target_weight"] = max(
            float(context.get("max_abs_target_weight", 0.0)),
            abs(float(meta.get("target_weight", 0.0) or 0.0)),
        )
        context["max_abs_score"] = max(
            float(context.get("max_abs_score", 0.0)),
            abs(float(meta.get("score", 0.0) or 0.0)),
        )
        context["max_coherence"] = max(
            float(context.get("max_coherence", 0.0)),
            abs(float(meta.get("coherence", 0.0) or 0.0)),
        )
        context["max_velocity_signal"] = max(
            float(context.get("max_velocity_signal", 0.0)),
            abs(float(meta.get("velocity_signal", 0.0) or 0.0)),
        )
        context["extreme_regime_seen"] = bool(context.get("extreme_regime_seen", False) or meta.get("extreme_regime", False))
        context["reallocation_seen"] = bool(
            context.get("reallocation_seen", False)
            or abs(float(meta.get("reallocation_target_weight", 0.0) or 0.0)) > 0.0
        )
        if meta.get("reason") is not None:
            context["last_reason"] = meta.get("reason")
        context["last_metadata"] = meta
        context["entry_price_hint"] = float(position_avg_price)
        if count_observation and timestamp is not None:
            observation_date = timestamp.date().isoformat()
            if context.get("last_observation_date") != observation_date:
                core_phase = max(0, min(int(meta.get("core_phase", 0) or 0), 3))
                overlay_phase = max(0, min(int(meta.get("overlay_phase", 0) or 0), 3))
                if core_phase > 0:
                    phase_key = f"phase_{core_phase}_days"
                    context[phase_key] = int(context.get(phase_key, 0)) + 1
                if overlay_phase > 0:
                    overlay_key = f"overlay_phase_{overlay_phase}_days"
                    context[overlay_key] = int(context.get(overlay_key, 0)) + 1
                if bool(meta.get("extreme_regime", False)):
                    context["extreme_regime_days"] = int(context.get("extreme_regime_days", 0)) + 1
                if abs(float(meta.get("reallocation_target_weight", 0.0) or 0.0)) > 0.0:
                    context["reallocation_days"] = int(context.get("reallocation_days", 0)) + 1
                context["last_observation_date"] = observation_date

    def update_trade_context(
        self,
        *,
        symbol: str,
        metadata: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        position = self.position_for_symbol(symbol)
        if isclose(position.quantity, 0.0):
            return
        direction = 1 if position.quantity > 0 else -1
        context = self._open_trade_context.get(symbol)
        if context is None or int(context.get("direction", 0)) != direction:
            synthetic_fill = FillEvent(
                timestamp=timestamp,
                symbol=symbol,
                side=Side.BUY if direction > 0 else Side.SELL,
                quantity=0,
                price=position.avg_price,
                commission=0.0,
                slippage_cost=0.0,
                order_id="context-sync",
                metadata=dict(metadata),
            )
            self._init_trade_context(
                symbol=symbol,
                fill=synthetic_fill,
                direction=direction,
                entry_price=position.avg_price,
            )
            return
        self._merge_trade_context_metadata(
            symbol=symbol,
            metadata=metadata,
            direction=direction,
            position_avg_price=position.avg_price,
            timestamp=timestamp,
            count_observation=True,
        )

    def _build_closed_trade_metadata(
        self,
        *,
        symbol: str,
        fill: FillEvent,
        direction: int,
        entry_price: float,
    ) -> tuple[datetime | None, dict[str, Any]]:
        context = self._open_trade_context.get(symbol, {})
        exit_meta = dict(fill.metadata)
        entry_timestamp = context.get("entry_timestamp")
        metadata: dict[str, Any] = {
            "strategy": context.get("strategy", exit_meta.get("strategy")),
            "engine": context.get("engine", exit_meta.get("engine")),
            "entry_reason": context.get("entry_reason"),
            "exit_reason": exit_meta.get("reason"),
            "entry_region": context.get("entry_region"),
            "entry_theme": context.get("entry_theme"),
            "entry_asset_class": context.get("entry_asset_class"),
            "entry_target_weight": float(context.get("entry_target_weight", 0.0) or 0.0),
            "entry_core_phase": int(context.get("entry_core_phase", 0) or 0),
            "entry_overlay_phase": int(context.get("entry_overlay_phase", 0) or 0),
            "max_core_phase": int(context.get("max_core_phase", 0) or 0),
            "max_overlay_phase": int(context.get("max_overlay_phase", 0) or 0),
            "max_abs_target_weight": float(context.get("max_abs_target_weight", 0.0) or 0.0),
            "max_abs_score": float(context.get("max_abs_score", 0.0) or 0.0),
            "max_coherence": float(context.get("max_coherence", 0.0) or 0.0),
            "max_velocity_signal": float(context.get("max_velocity_signal", 0.0) or 0.0),
            "extreme_regime_seen": bool(context.get("extreme_regime_seen", False)),
            "reallocation_seen": bool(context.get("reallocation_seen", False)),
            "phase_1_days": int(context.get("phase_1_days", 0) or 0),
            "phase_2_days": int(context.get("phase_2_days", 0) or 0),
            "phase_3_days": int(context.get("phase_3_days", 0) or 0),
            "overlay_phase_1_days": int(context.get("overlay_phase_1_days", 0) or 0),
            "overlay_phase_2_days": int(context.get("overlay_phase_2_days", 0) or 0),
            "overlay_phase_3_days": int(context.get("overlay_phase_3_days", 0) or 0),
            "extreme_regime_days": int(context.get("extreme_regime_days", 0) or 0),
            "reallocation_days": int(context.get("reallocation_days", 0) or 0),
            "direction_label": "LONG" if direction > 0 else "SHORT",
            "days_held": (
                max((fill.timestamp.date() - entry_timestamp.date()).days, 0)
                if isinstance(entry_timestamp, datetime)
                else 0
            ),
            "entry_price_hint": float(context.get("entry_price_hint", entry_price) or entry_price),
            "entry_metadata": deepcopy(context.get("entry_metadata", {})),
            "exit_metadata": exit_meta,
        }
        return (entry_timestamp if isinstance(entry_timestamp, datetime) else None, metadata)

    def on_fill(self, fill: FillEvent) -> None:
        symbol = fill.symbol.upper()
        position = self.position_for_symbol(symbol)
        fill_qty = float(fill.quantity)
        signed_fill_qty = fill_qty if fill.side == Side.BUY else -fill_qty
        multiplier = self._multiplier(symbol)
        is_futures = self._is_futures_symbol(symbol)

        if not is_futures:
            if fill.side == Side.BUY:
                self.cash -= fill_qty * fill.price
            else:
                self.cash += fill_qty * fill.price
        self.cash -= fill.commission

        old_qty = position.quantity
        old_avg = position.avg_price

        if isclose(old_qty, 0.0):
            position.quantity = signed_fill_qty
            position.avg_price = fill.price
            self._init_trade_context(
                symbol=symbol,
                fill=fill,
                direction=1 if signed_fill_qty > 0 else -1,
                entry_price=fill.price,
            )
            if not isclose(position.quantity, 0.0):
                self._risk_exit_pending.discard(symbol)
            return

        same_direction = (old_qty > 0 and signed_fill_qty > 0) or (old_qty < 0 and signed_fill_qty < 0)
        if same_direction:
            total_qty = abs(old_qty) + abs(signed_fill_qty)
            if total_qty > 0:
                position.avg_price = (
                    (abs(old_qty) * old_avg + abs(signed_fill_qty) * fill.price) / total_qty
                )
            position.quantity = old_qty + signed_fill_qty
            self._merge_trade_context(
                symbol=symbol,
                fill=fill,
                direction=1 if position.quantity > 0 else -1,
                position_avg_price=position.avg_price,
            )
            self._risk_exit_pending.discard(symbol)
            return

        closing_qty = min(abs(old_qty), abs(signed_fill_qty))
        direction = 1.0 if old_qty > 0 else -1.0
        realized = closing_qty * (fill.price - old_avg) * direction * multiplier
        position.realized_pnl += realized
        if is_futures:
            self.cash += realized
        entry_timestamp, trade_metadata = self._build_closed_trade_metadata(
            symbol=symbol,
            fill=fill,
            direction=1 if old_qty > 0 else -1,
            entry_price=old_avg,
        )
        self._record_realized_trade(
            symbol=symbol,
            timestamp=fill.timestamp,
            quantity=closing_qty,
            pnl=realized,
            entry_timestamp=entry_timestamp,
            entry_price=old_avg,
            exit_price=fill.price,
            direction=1 if old_qty > 0 else -1,
            metadata=trade_metadata,
        )

        new_qty = old_qty + signed_fill_qty
        if isclose(new_qty, 0.0):
            position.quantity = 0.0
            position.avg_price = 0.0
            self._open_trade_context.pop(symbol, None)
            self._risk_exit_pending.discard(symbol)
            return

        if (old_qty > 0 and new_qty > 0) or (old_qty < 0 and new_qty < 0):
            position.quantity = new_qty
            self._merge_trade_context(
                symbol=symbol,
                fill=fill,
                direction=1 if new_qty > 0 else -1,
                position_avg_price=position.avg_price,
            )
            self._risk_exit_pending.discard(symbol)
            return

        # Position flipped (reversal): remainder opens at fill price.
        position.quantity = new_qty
        position.avg_price = fill.price
        self._init_trade_context(
            symbol=symbol,
            fill=fill,
            direction=1 if new_qty > 0 else -1,
            entry_price=fill.price,
        )
        self._risk_exit_pending.discard(symbol)

    def apply_borrow_fees(
        self,
        *,
        timestamp: datetime,
        bars: dict[str, Bar],
        borrow_source: BorrowDataSource | None,
        previous_timestamp: datetime | None,
    ) -> float:
        if borrow_source is None or previous_timestamp is None:
            return 0.0
        elapsed_days = max((timestamp - previous_timestamp).total_seconds() / 86_400.0, 0.0)
        if elapsed_days <= 0.0:
            return 0.0

        fee_total = 0.0
        for symbol, position in self.positions.items():
            if self._is_futures_symbol(symbol):
                continue
            if position.quantity >= 0 or isclose(position.quantity, 0.0):
                continue
            bar = bars.get(symbol) or self.latest_bars.get(symbol)
            if bar is None:
                continue
            snapshot = borrow_source.snapshot(symbol, timestamp.date())
            if snapshot is None:
                continue
            rate = max(float(snapshot.annualized_fee), 0.0)
            fee = abs(self._notional_value(symbol, position.quantity, bar.close)) * rate * elapsed_days / 365.0
            if fee > 0:
                self.cash -= fee
                fee_total += fee

        self.borrow_fees_paid += fee_total
        return fee_total

    @staticmethod
    def _rate_on(rate: RateInput, as_of: date) -> float:
        value = rate(as_of) if callable(rate) else rate
        value = float(value)
        return 0.0 if not np.isfinite(value) else value

    def apply_financing(self, *, timestamp: datetime, previous_timestamp: datetime | None) -> float:
        """Accrue interest on the cash balance held since the previous bar.

        Short-sale proceeds are held by the broker as collateral, so financing uses cash net
        of them. A negative net balance (longs bought on margin) pays the margin rate; a
        positive one earns the cash rate. Returns the net amount charged (positive = cost).
        """
        if previous_timestamp is None:
            return 0.0
        elapsed_days = max((timestamp - previous_timestamp).total_seconds() / 86_400.0, 0.0)
        if elapsed_days <= 0.0:
            return 0.0

        as_of = previous_timestamp.date()
        year_fraction = elapsed_days / 365.0
        short_proceeds = 0.0
        for symbol, position in self.positions.items():
            if position.quantity >= 0 or self._is_futures_symbol(symbol):
                continue
            bar = self.latest_bars.get(symbol)
            price = bar.close if bar is not None else position.avg_price
            short_proceeds += abs(self._notional_value(symbol, position.quantity, price))
        net_cash = self.cash - short_proceeds

        if net_cash < 0.0:
            cost = -net_cash * max(self._rate_on(self.margin_interest_rate, as_of), 0.0) * year_fraction
            self.cash -= cost
            self.margin_interest_paid += cost
            return cost

        earned = net_cash * self._rate_on(self.cash_interest_rate, as_of) * year_fraction
        self.cash += earned
        self.cash_interest_earned += earned
        return -earned

    def mark_to_market(self, timestamp: datetime, bars: dict[str, Bar]) -> PortfolioSnapshot:
        self.latest_bars.update({symbol.upper(): bar for symbol, bar in bars.items()})

        gross_exposure = 0.0
        net_exposure = 0.0
        unrealized_pnl = 0.0
        realized_pnl = sum(position.realized_pnl for position in self.positions.values())
        marked_position_value = 0.0

        for symbol, position in self.positions.items():
            if isclose(position.quantity, 0.0):
                continue
            bar = bars.get(symbol) or self.latest_bars.get(symbol)
            if bar is None:
                continue
            exposure_value = self._notional_value(symbol, position.quantity, bar.close)
            marked_value = self._unrealized_value(symbol, position, bar.close)
            marked_position_value += marked_value
            net_exposure += exposure_value
            gross_exposure += abs(exposure_value)
            if self._is_futures_symbol(symbol):
                unrealized_pnl += marked_value
            else:
                unrealized_pnl += position.quantity * (bar.close - position.avg_price)

        equity = self.cash + marked_position_value
        self.latest_equity = equity

        used_margin = gross_exposure / self.leverage
        margin_ratio = np.inf if used_margin == 0 else equity / used_margin
        margin_call = bool(used_margin > 0 and margin_ratio < self.maintenance_margin_ratio)

        snapshot = PortfolioSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            equity=equity,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            used_margin=used_margin,
            margin_ratio=float(margin_ratio),
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            borrow_fees_paid=self.borrow_fees_paid,
            margin_call=margin_call,
        )
        self.history.append(snapshot)
        return snapshot

    def generate_margin_liquidation_orders(self, timestamp: datetime) -> list[OrderEvent]:
        orders: list[OrderEvent] = []
        for symbol, position in self.positions.items():
            qty = int(abs(position.quantity))
            if qty <= 0:
                continue
            side = Side.SELL if position.quantity > 0 else Side.BUY
            orders.append(
                OrderEvent(
                    timestamp=timestamp,
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    order_type=OrderType.MARKET,
                    metadata={"reason": "MARGIN_CALL"},
                )
            )
            self._risk_exit_pending.add(symbol)
        return orders

    def generate_hard_stop_orders(self, timestamp: datetime, bars: dict[str, Bar]) -> list[OrderEvent]:
        if self.hard_stop_loss_pct is None or self.hard_stop_loss_pct <= 0:
            return []
        orders: list[OrderEvent] = []
        stop = float(self.hard_stop_loss_pct)

        for symbol, position in self.positions.items():
            qty = int(abs(position.quantity))
            if qty <= 0 or symbol in self._risk_exit_pending:
                continue
            bar = bars.get(symbol) or self.latest_bars.get(symbol)
            if bar is None or position.avg_price <= 0:
                continue

            if position.quantity > 0 and bar.close <= position.avg_price * (1.0 - stop):
                orders.append(
                    OrderEvent(
                        timestamp=timestamp,
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=qty,
                        order_type=OrderType.MARKET,
                        metadata={"reason": "HARD_STOP_LOSS"},
                    )
                )
                self._risk_exit_pending.add(symbol)
            elif position.quantity < 0 and bar.close >= position.avg_price * (1.0 + stop):
                orders.append(
                    OrderEvent(
                        timestamp=timestamp,
                        symbol=symbol,
                        side=Side.BUY,
                        quantity=qty,
                        order_type=OrderType.MARKET,
                        metadata={"reason": "HARD_STOP_LOSS"},
                    )
                )
                self._risk_exit_pending.add(symbol)

        return orders
