from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from quantbt.altdata.base import IndexMembershipDataSource


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


@dataclass(slots=True)
class PointInTimeIndexUniverse:
    membership_source: IndexMembershipDataSource
    index_name: str

    def members(self, as_of: date) -> list[str]:
        return self.membership_source.members(self.index_name, as_of)

    def is_member(self, symbol: str, as_of: date) -> bool:
        return self.membership_source.is_member(self.index_name, symbol, as_of)


class HistoricalUniverseBuilder:
    def __init__(self, membership_source: IndexMembershipDataSource) -> None:
        self.membership_source = membership_source

    def for_index(self, index_name: str) -> PointInTimeIndexUniverse:
        normalized = _normalize_index_name(index_name)
        return PointInTimeIndexUniverse(
            membership_source=self.membership_source,
            index_name=normalized,
        )

    def sp500(self) -> PointInTimeIndexUniverse:
        return self.for_index("SP500")

    def sp1500(self) -> PointInTimeIndexUniverse:
        return self.for_index("SP1500")

    def russell2000(self) -> PointInTimeIndexUniverse:
        return self.for_index("RUSSELL2000")

