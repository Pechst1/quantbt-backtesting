from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(slots=True)
class EarningsAnnouncement:
    symbol: str
    announcement_ts: datetime
    eps_estimate: float
    eps_actual: float
    estimate_std: float
    next_announcement_ts: datetime | None = None

    @property
    def sue(self) -> float:
        if self.estimate_std <= 0:
            return 0.0
        return (self.eps_actual - self.eps_estimate) / self.estimate_std


@dataclass(slots=True)
class FundamentalSnapshot:
    symbol: str
    as_of: datetime
    net_income_ttm: float
    shares_outstanding: float
    revenue_ttm: float | None = None
    eps_growth_yoy: float | None = None
    revenue_growth_yoy: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None
    cash_per_share: float | None = None
    book_value_per_share: float | None = None


@dataclass(slots=True)
class CorporateAction:
    symbol: str
    action_date: date
    action_type: str
    value: float = 0.0
    notes: str = ""


@dataclass(slots=True)
class BorrowSnapshot:
    symbol: str
    as_of: date
    annualized_fee: float
    is_hard_to_borrow: bool
    short_interest_pct: float | None = None


@dataclass(slots=True)
class FactorPoint:
    as_of: date
    market_excess: float
    smb: float
    hml: float
    rf: float


@dataclass(slots=True)
class CatalystEvent:
    symbol: str
    effective_from: date
    effective_to: date | None = None
    catalyst_type: str = ""
    direction: int = 0
    strength: float = 1.0
    target_price: float | None = None
    notes: str = ""


@dataclass(slots=True)
class MacroSnapshot:
    region: str
    as_of: date
    release_date: date
    tradable_from: date
    growth_surprise: float
    inflation_surprise: float
    policy_surprise: float
    liquidity_surprise: float
    credit_surprise: float
    snapshot_id: str = ""
    quality_score: float = 1.0
    coverage_ratio: float = 1.0
    source_label: str = ""

    @property
    def composite_score(self) -> float:
        return (
            self.growth_surprise
            + self.inflation_surprise
            + self.policy_surprise
            + self.liquidity_surprise
            + self.credit_surprise
        )

    @property
    def cache_key(self) -> tuple[str, date, date, date]:
        return (
            self.snapshot_id or self.region,
            self.as_of,
            self.release_date,
            self.tradable_from,
        )


@dataclass(slots=True)
class MacroSurpriseEvent:
    region: str
    factor: str
    release_ts: datetime
    tradable_from: datetime
    surprise_z: float
    source_label: str = ""

    @property
    def release_date(self) -> date:
        return self.release_ts.date()

    @property
    def tradable_date(self) -> date:
        return self.tradable_from.date()


@dataclass(slots=True)
class VolatilitySnapshot:
    symbol: str
    as_of: date
    implied_vol: float | None = None
    implied_vol_percentile: float = 50.0
    event_risk_score: float = 0.0
    realized_vol: float | None = None


@dataclass(slots=True)
class FuturesCurveSnapshot:
    symbol: str
    as_of: date
    release_date: date
    tradable_from: date
    front_price: float | None = None
    second_price: float | None = None
    third_price: float | None = None
    fourth_price: float | None = None
    front_contract: str = ""
    second_contract: str = ""
    third_contract: str = ""
    fourth_contract: str = ""
    roll_yield_1m: float = 0.0
    roll_yield_3m: float = 0.0
    carry_score: float = 0.0
    backwardation_score: float = 0.0
    liquidity_score: float = 1.0
    source_label: str = ""
