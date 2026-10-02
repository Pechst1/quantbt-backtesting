from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

import pandas as pd

from quantbt.altdata.models import (
    BorrowSnapshot,
    CatalystEvent,
    CorporateAction,
    EarningsAnnouncement,
    FundamentalSnapshot,
    FuturesCurveSnapshot,
    MacroSnapshot,
    MacroSurpriseEvent,
    VolatilitySnapshot,
)


class EarningsDataSource(ABC):
    @abstractmethod
    def announcements_on(self, as_of: date) -> list[EarningsAnnouncement]:
        ...

    @abstractmethod
    def next_announcement_after(self, symbol: str, as_of: date) -> datetime | None:
        ...


class FundamentalsDataSource(ABC):
    @abstractmethod
    def snapshot(self, symbol: str, as_of: date) -> FundamentalSnapshot | None:
        ...


class CorporateActionsDataSource(ABC):
    @abstractmethod
    def actions_on(self, symbol: str, as_of: date) -> list[CorporateAction]:
        ...

    def has_action(
        self,
        *,
        symbol: str,
        as_of: date,
        action_types: set[str] | None = None,
    ) -> bool:
        actions = self.actions_on(symbol, as_of)
        if action_types is None:
            return len(actions) > 0
        expected = {action.upper() for action in action_types}
        return any(action.action_type.upper() in expected for action in actions)


class BorrowDataSource(ABC):
    @abstractmethod
    def snapshot(self, symbol: str, as_of: date) -> BorrowSnapshot | None:
        ...


class CatalystDataSource(ABC):
    @abstractmethod
    def active_events(self, symbol: str, as_of: date) -> list[CatalystEvent]:
        ...


class MacroDataSource(ABC):
    @abstractmethod
    def snapshot(self, region: str, as_of: date) -> MacroSnapshot | None:
        ...

    @abstractmethod
    def history(self, region: str, as_of: date, lookback: int | None = None) -> list[MacroSnapshot]:
        ...

    @abstractmethod
    def regions(self) -> list[str]:
        ...


class MacroSurpriseDataSource(ABC):
    @abstractmethod
    def events(self, region: str, as_of: date, lookback_days: int | None = None) -> list[MacroSurpriseEvent]:
        ...


class VolatilityDataSource(ABC):
    @abstractmethod
    def snapshot(self, symbol: str, as_of: date) -> VolatilitySnapshot | None:
        ...


class FuturesCurveDataSource(ABC):
    @abstractmethod
    def snapshot(self, symbol: str, as_of: date) -> FuturesCurveSnapshot | None:
        ...


class FactorDataSource(ABC):
    @abstractmethod
    def factor_window(self, dates: pd.Index) -> pd.DataFrame:
        """
        Returns a DataFrame indexed by dates with columns:
        market_excess, smb, hml, rf
        """


class IndexMembershipDataSource(ABC):
    @abstractmethod
    def members(self, index_name: str, as_of: date) -> list[str]:
        ...

    @abstractmethod
    def is_member(self, index_name: str, symbol: str, as_of: date) -> bool:
        ...

    @abstractmethod
    def available_indices(self) -> list[str]:
        ...
