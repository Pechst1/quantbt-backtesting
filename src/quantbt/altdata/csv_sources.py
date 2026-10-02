from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.altdata.base import (
    BorrowDataSource,
    CatalystDataSource,
    CorporateActionsDataSource,
    EarningsDataSource,
    FactorDataSource,
    FundamentalsDataSource,
    FuturesCurveDataSource,
    IndexMembershipDataSource,
    MacroDataSource,
    VolatilityDataSource,
)
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


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


class CSVEarningsDataSource(EarningsDataSource):
    """
    Expected columns:
    symbol, announcement_ts, eps_estimate, eps_actual, estimate_std, next_announcement_ts(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "announcement_ts", "eps_estimate", "eps_actual", "estimate_std"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in earnings CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["announcement_ts"] = pd.to_datetime(frame["announcement_ts"], utc=True).dt.tz_localize(None)
        frame["eps_estimate"] = pd.to_numeric(frame["eps_estimate"], errors="coerce")
        frame["eps_actual"] = pd.to_numeric(frame["eps_actual"], errors="coerce")
        frame["estimate_std"] = pd.to_numeric(frame["estimate_std"], errors="coerce")
        if "next_announcement_ts" in frame.columns:
            frame["next_announcement_ts"] = pd.to_datetime(
                frame["next_announcement_ts"],
                errors="coerce",
                utc=True,
            ).dt.tz_localize(None)
        else:
            frame["next_announcement_ts"] = pd.NaT
        frame = frame.dropna(subset=["announcement_ts", "eps_estimate", "eps_actual", "estimate_std"])
        frame = frame.sort_values(["announcement_ts", "symbol"]).reset_index(drop=True)
        frame["announcement_date"] = frame["announcement_ts"].dt.date
        self._frame = frame

    def announcements_on(self, as_of: date) -> list[EarningsAnnouncement]:
        d = _to_date(as_of)
        rows = self._frame[self._frame["announcement_date"] == d]
        return [
            EarningsAnnouncement(
                symbol=str(row.symbol),
                announcement_ts=pd.Timestamp(row.announcement_ts).to_pydatetime(),
                eps_estimate=float(row.eps_estimate),
                eps_actual=float(row.eps_actual),
                estimate_std=float(row.estimate_std),
                next_announcement_ts=(
                    pd.Timestamp(row.next_announcement_ts).to_pydatetime()
                    if pd.notna(row.next_announcement_ts)
                    else None
                ),
            )
            for row in rows.itertuples()
        ]

    def next_announcement_after(self, symbol: str, as_of: date) -> datetime | None:
        d = _to_date(as_of)
        rows = self._frame[
            (self._frame["symbol"] == symbol.upper()) & (self._frame["announcement_date"] > d)
        ]
        if rows.empty:
            return None
        return pd.Timestamp(rows.iloc[0]["announcement_ts"]).to_pydatetime()


class CSVFundamentalsDataSource(FundamentalsDataSource):
    """
    Expected columns:
    symbol, effective_from, net_income_ttm, shares_outstanding, effective_to(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "effective_from", "net_income_ttm", "shares_outstanding"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in fundamentals CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["effective_from"] = pd.to_datetime(frame["effective_from"], utc=True).dt.tz_localize(None)
        frame["effective_to"] = pd.to_datetime(
            frame.get("effective_to", pd.Series(pd.NaT, index=frame.index)),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        frame["net_income_ttm"] = pd.to_numeric(frame["net_income_ttm"], errors="coerce")
        frame["shares_outstanding"] = pd.to_numeric(frame["shares_outstanding"], errors="coerce")
        optional_numeric = (
            "revenue_ttm",
            "eps_growth_yoy",
            "revenue_growth_yoy",
            "pe_ratio",
            "pb_ratio",
            "ev_ebitda",
            "fcf_yield",
            "cash_per_share",
            "book_value_per_share",
        )
        for col in optional_numeric:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.sort_values(["symbol", "effective_from"]).reset_index(drop=True)
        self._frame = frame

    def snapshot(self, symbol: str, as_of: date) -> FundamentalSnapshot | None:
        symbol = symbol.upper().strip()
        dt = pd.Timestamp(_to_date(as_of))
        rows = self._frame[self._frame["symbol"] == symbol]
        if rows.empty:
            return None
        valid = rows[rows["effective_from"] <= dt]
        if valid.empty:
            return None
        if "effective_to" in valid.columns:
            valid = valid[(valid["effective_to"].isna()) | (valid["effective_to"] >= dt)]
            if valid.empty:
                return None
        row = valid.iloc[-1]
        return FundamentalSnapshot(
            symbol=symbol,
            as_of=pd.Timestamp(row["effective_from"]).to_pydatetime(),
            net_income_ttm=float(row["net_income_ttm"]),
            shares_outstanding=float(row["shares_outstanding"]),
            revenue_ttm=(
                float(row["revenue_ttm"]) if "revenue_ttm" in row.index and pd.notna(row["revenue_ttm"]) else None
            ),
            eps_growth_yoy=(
                float(row["eps_growth_yoy"])
                if "eps_growth_yoy" in row.index and pd.notna(row["eps_growth_yoy"])
                else None
            ),
            revenue_growth_yoy=(
                float(row["revenue_growth_yoy"])
                if "revenue_growth_yoy" in row.index and pd.notna(row["revenue_growth_yoy"])
                else None
            ),
            pe_ratio=float(row["pe_ratio"]) if "pe_ratio" in row.index and pd.notna(row["pe_ratio"]) else None,
            pb_ratio=float(row["pb_ratio"]) if "pb_ratio" in row.index and pd.notna(row["pb_ratio"]) else None,
            ev_ebitda=(
                float(row["ev_ebitda"]) if "ev_ebitda" in row.index and pd.notna(row["ev_ebitda"]) else None
            ),
            fcf_yield=(
                float(row["fcf_yield"]) if "fcf_yield" in row.index and pd.notna(row["fcf_yield"]) else None
            ),
            cash_per_share=(
                float(row["cash_per_share"])
                if "cash_per_share" in row.index and pd.notna(row["cash_per_share"])
                else None
            ),
            book_value_per_share=(
                float(row["book_value_per_share"])
                if "book_value_per_share" in row.index and pd.notna(row["book_value_per_share"])
                else None
            ),
        )


class CSVCorporateActionsDataSource(CorporateActionsDataSource):
    """
    Expected columns:
    symbol, action_date, action_type, value(optional), notes(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "action_date", "action_type"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in corporate actions CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["action_date"] = pd.to_datetime(frame["action_date"], utc=True).dt.date
        frame["action_type"] = frame["action_type"].astype(str).str.upper().str.strip()
        frame["value"] = pd.to_numeric(frame.get("value", 0.0), errors="coerce").fillna(0.0)
        frame["notes"] = frame.get("notes", "").astype(str)
        self._frame = frame.sort_values(["action_date", "symbol"]).reset_index(drop=True)

    def actions_on(self, symbol: str, as_of: date) -> list[CorporateAction]:
        symbol = symbol.upper().strip()
        d = _to_date(as_of)
        rows = self._frame[(self._frame["symbol"] == symbol) & (self._frame["action_date"] == d)]
        return [
            CorporateAction(
                symbol=symbol,
                action_date=row.action_date,
                action_type=str(row.action_type),
                value=float(row.value),
                notes=str(row.notes),
            )
            for row in rows.itertuples()
        ]


class CSVBorrowDataSource(BorrowDataSource):
    """
    Expected columns:
    symbol, date, annualized_fee, is_hard_to_borrow, short_interest_pct(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "date", "annualized_fee", "is_hard_to_borrow"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in borrow CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.date
        frame["annualized_fee"] = pd.to_numeric(frame["annualized_fee"], errors="coerce").fillna(0.0)
        frame["is_hard_to_borrow"] = frame["is_hard_to_borrow"].astype(bool)
        frame["short_interest_pct"] = pd.to_numeric(
            frame.get("short_interest_pct", np.nan),
            errors="coerce",
        )
        self._frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)

    def snapshot(self, symbol: str, as_of: date) -> BorrowSnapshot | None:
        symbol = symbol.upper().strip()
        d = _to_date(as_of)
        rows = self._frame[(self._frame["symbol"] == symbol) & (self._frame["date"] <= d)]
        if rows.empty:
            return None
        row = rows.iloc[-1]
        return BorrowSnapshot(
            symbol=symbol,
            as_of=row["date"],
            annualized_fee=float(row["annualized_fee"]),
            is_hard_to_borrow=bool(row["is_hard_to_borrow"]),
            short_interest_pct=(
                float(row["short_interest_pct"]) if pd.notna(row["short_interest_pct"]) else None
            ),
        )


class StaticBorrowDataSource(BorrowDataSource):
    def __init__(
        self,
        annualized_fee: float = 0.03,
        hard_to_borrow_symbols: set[str] | None = None,
    ) -> None:
        self.annualized_fee = max(float(annualized_fee), 0.0)
        self.hard_to_borrow_symbols = {symbol.upper() for symbol in (hard_to_borrow_symbols or set())}

    def snapshot(self, symbol: str, as_of: date) -> BorrowSnapshot | None:
        symbol = symbol.upper().strip()
        return BorrowSnapshot(
            symbol=symbol,
            as_of=_to_date(as_of),
            annualized_fee=self.annualized_fee,
            is_hard_to_borrow=symbol in self.hard_to_borrow_symbols,
        )


class CSVFactorDataSource(FactorDataSource):
    """
    Expected columns:
    date, market_excess (or mkt_rf), smb, hml, rf
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        if "mkt_rf" in frame.columns and "market_excess" not in frame.columns:
            frame = frame.rename(columns={"mkt_rf": "market_excess"})
        required = {"date", "market_excess", "smb", "hml", "rf"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in factor CSV: {sorted(missing)}")

        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
        for col in ("market_excess", "smb", "hml", "rf"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
        self._frame = frame.set_index("date")[["market_excess", "smb", "hml", "rf"]].sort_index()

    def factor_window(self, dates: pd.Index) -> pd.DataFrame:
        idx = pd.to_datetime(dates).tz_localize(None)
        factors = self._frame.reindex(idx).ffill().fillna(0.0)
        factors.index = idx
        return factors


class IBESEarningsCSVSource(CSVEarningsDataSource):
    """CSV adapter for IBES/FactSet/Zacks-style historical earnings surprises."""


class CompustatFundamentalsCSVSource(CSVFundamentalsDataSource):
    """CSV adapter for point-in-time Compustat fundamentals."""


class CRSPCorporateActionsCSVSource(CSVCorporateActionsDataSource):
    """CSV adapter for CRSP-like corporate actions and delisting events."""


class MarkitBorrowCSVSource(CSVBorrowDataSource):
    """CSV adapter for Markit-like borrow fee and hard-to-borrow history."""


class CSVSecurityMasterDataSource:
    """
    Survivorship-bias mitigation helper.

    Expected columns:
    symbol, list_date, delist_date(optional), sector(optional), industry(optional),
    region(optional), asset_class(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "list_date"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in security master CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["list_date"] = pd.to_datetime(frame["list_date"], utc=True).dt.tz_localize(None)
        frame["delist_date"] = pd.to_datetime(
            frame.get("delist_date", pd.Series(pd.NaT, index=frame.index)),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        frame["sector"] = frame.get("sector", "").astype(str)
        frame["industry"] = frame.get("industry", "").astype(str)
        frame["region"] = frame.get("region", "").astype(str)
        frame["asset_class"] = frame.get("asset_class", "").astype(str)
        self._frame = frame.sort_values(["symbol", "list_date"]).reset_index(drop=True)

    def active_symbols(self, as_of: date) -> list[str]:
        dt = pd.Timestamp(_to_date(as_of))
        frame = self._frame[self._frame["list_date"] <= dt]
        frame = frame[(frame["delist_date"].isna()) | (frame["delist_date"] >= dt)]
        return sorted(frame["symbol"].unique().tolist())

    def sector_map(self, as_of: date) -> dict[str, str]:
        return self.metadata_map(as_of, field="sector")

    def industry_map(self, as_of: date) -> dict[str, str]:
        return self.metadata_map(as_of, field="industry")

    def region_map(self, as_of: date) -> dict[str, str]:
        return self.metadata_map(as_of, field="region")

    def asset_class_map(self, as_of: date) -> dict[str, str]:
        return self.metadata_map(as_of, field="asset_class")

    def metadata_map(self, as_of: date, field: str) -> dict[str, str]:
        dt = pd.Timestamp(_to_date(as_of))
        frame = self._frame[self._frame["list_date"] <= dt]
        frame = frame[(frame["delist_date"].isna()) | (frame["delist_date"] >= dt)]
        latest = frame.sort_values("list_date").groupby("symbol").tail(1)
        return {
            str(row.symbol): str(getattr(row, field))
            for row in latest.itertuples()
            if isinstance(getattr(row, field), str) and str(getattr(row, field)).strip()
        }


class CSVIndexMembershipDataSource(IndexMembershipDataSource):
    """
    Expected columns:
    index_name, symbol, effective_from, effective_to(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"index_name", "symbol", "effective_from"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in index membership CSV: {sorted(missing)}")

        frame["index_name"] = frame["index_name"].astype(str).str.upper().str.strip()
        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["effective_from"] = pd.to_datetime(frame["effective_from"], utc=True).dt.tz_localize(None)
        frame["effective_to"] = pd.to_datetime(
            frame.get("effective_to", pd.Series(pd.NaT, index=frame.index)),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        frame = frame.sort_values(["index_name", "symbol", "effective_from"]).reset_index(drop=True)
        self._frame = frame

    def _active_rows(self, index_name: str, as_of: date) -> pd.DataFrame:
        idx = index_name.upper().strip()
        ts = pd.Timestamp(_to_date(as_of))
        frame = self._frame[self._frame["index_name"] == idx]
        frame = frame[frame["effective_from"] <= ts]
        frame = frame[(frame["effective_to"].isna()) | (frame["effective_to"] >= ts)]
        return frame

    def members(self, index_name: str, as_of: date) -> list[str]:
        frame = self._active_rows(index_name, as_of)
        return sorted(frame["symbol"].unique().tolist())

    def is_member(self, index_name: str, symbol: str, as_of: date) -> bool:
        symbol = symbol.upper().strip()
        frame = self._active_rows(index_name, as_of)
        return bool((frame["symbol"] == symbol).any())

    def available_indices(self) -> list[str]:
        return sorted(self._frame["index_name"].unique().tolist())


class CSVCatalystDataSource(CatalystDataSource):
    """
    Expected columns:
    symbol, effective_from, effective_to(optional), catalyst_type, direction, strength(optional),
    target_price(optional), notes(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "effective_from", "catalyst_type", "direction"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in catalyst CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["effective_from"] = pd.to_datetime(frame["effective_from"], utc=True).dt.tz_localize(None)
        frame["effective_to"] = pd.to_datetime(
            frame.get("effective_to", pd.Series(pd.NaT, index=frame.index)),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        frame["catalyst_type"] = frame["catalyst_type"].astype(str).str.upper().str.strip()
        frame["direction"] = pd.to_numeric(frame["direction"], errors="coerce").fillna(0).astype(int)
        frame["strength"] = pd.to_numeric(frame.get("strength", 1.0), errors="coerce").fillna(1.0)
        frame["target_price"] = pd.to_numeric(frame.get("target_price", np.nan), errors="coerce")
        frame["notes"] = frame.get("notes", "").astype(str)
        self._frame = frame.sort_values(["symbol", "effective_from"]).reset_index(drop=True)

    def active_events(self, symbol: str, as_of: date) -> list[CatalystEvent]:
        symbol = symbol.upper().strip()
        ts = pd.Timestamp(_to_date(as_of))
        rows = self._frame[self._frame["symbol"] == symbol]
        rows = rows[rows["effective_from"] <= ts]
        rows = rows[(rows["effective_to"].isna()) | (rows["effective_to"] >= ts)]
        return [
            CatalystEvent(
                symbol=symbol,
                effective_from=row.effective_from.date(),
                effective_to=row.effective_to.date() if pd.notna(row.effective_to) else None,
                catalyst_type=str(row.catalyst_type),
                direction=int(row.direction),
                strength=float(row.strength),
                target_price=float(row.target_price) if pd.notna(row.target_price) else None,
                notes=str(row.notes),
            )
            for row in rows.itertuples()
        ]


class CSVMacroDataSource(MacroDataSource):
    """
    Expected columns:
    date, region, growth_surprise, inflation_surprise, policy_surprise, liquidity_surprise, credit_surprise,
    release_date(optional), tradable_from(optional), snapshot_id(optional),
    quality_score(optional), coverage_ratio(optional), source_label(optional)
    """

    def __init__(self, path: str | Path, publication_lag_business_days: int = 5) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {
            "date",
            "region",
            "growth_surprise",
            "inflation_surprise",
            "policy_surprise",
            "liquidity_surprise",
            "credit_surprise",
        }
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in macro CSV: {sorted(missing)}")

        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
        frame["region"] = frame["region"].astype(str).str.upper().str.strip()
        frame["release_date"] = pd.to_datetime(
            frame.get("release_date", frame["date"]),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        default_tradable = frame["release_date"] + pd.offsets.BDay(max(int(publication_lag_business_days), 0))
        frame["tradable_from"] = pd.to_datetime(
            frame.get("tradable_from", default_tradable),
            errors="coerce",
            utc=True,
        ).dt.tz_localize(None)
        frame["snapshot_id"] = frame.get("snapshot_id", pd.Series("", index=frame.index)).astype(str).str.strip()
        frame["quality_score"] = pd.to_numeric(
            frame.get("quality_score", pd.Series(1.0, index=frame.index)),
            errors="coerce",
        ).fillna(1.0)
        frame["coverage_ratio"] = pd.to_numeric(
            frame.get("coverage_ratio", pd.Series(1.0, index=frame.index)),
            errors="coerce",
        ).fillna(1.0)
        frame["source_label"] = frame.get("source_label", pd.Series("", index=frame.index)).astype(str).str.strip()
        for col in (
            "growth_surprise",
            "inflation_surprise",
            "policy_surprise",
            "liquidity_surprise",
            "credit_surprise",
        ):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
        frame = frame.dropna(subset=["date", "region", "release_date", "tradable_from"])
        frame["quality_score"] = frame["quality_score"].clip(lower=0.0, upper=1.0)
        frame["coverage_ratio"] = frame["coverage_ratio"].clip(lower=0.0, upper=1.0)
        frame = frame.sort_values(["region", "date", "release_date", "tradable_from"]).reset_index(drop=True)

        if (frame["snapshot_id"] != "").any():
            # Event-driven macro datasets may have multiple PiT updates per observation period. In that case the
            # builder is responsible for emitting unique snapshot ids and first-release discipline per snapshot.
            frame = frame.drop_duplicates(subset=["region", "snapshot_id"], keep="first")
        else:
            # Legacy monthly regime files only have one valid first-release record per region/period.
            frame = frame.groupby(["region", "date"], as_index=False, sort=False).first()
        self._frame = frame.sort_values(
            ["region", "tradable_from", "release_date", "date", "snapshot_id"]
        ).reset_index(drop=True)

    def _eligible_rows(self, region: str, as_of: date) -> pd.DataFrame:
        region = region.upper().strip()
        ts = pd.Timestamp(_to_date(as_of))
        rows = self._frame[(self._frame["region"] == region) & (self._frame["tradable_from"] <= ts)]
        if rows.empty and region != "GLOBAL":
            rows = self._frame[(self._frame["region"] == "GLOBAL") & (self._frame["tradable_from"] <= ts)]
        return rows

    def snapshot(self, region: str, as_of: date) -> MacroSnapshot | None:
        rows = self._eligible_rows(region, as_of)
        if rows.empty:
            return None
        row = rows.iloc[-1]
        return MacroSnapshot(
            region=str(row["region"]),
            as_of=pd.Timestamp(row["date"]).date(),
            release_date=pd.Timestamp(row["release_date"]).date(),
            tradable_from=pd.Timestamp(row["tradable_from"]).date(),
            growth_surprise=float(row["growth_surprise"]),
            inflation_surprise=float(row["inflation_surprise"]),
            policy_surprise=float(row["policy_surprise"]),
            liquidity_surprise=float(row["liquidity_surprise"]),
            credit_surprise=float(row["credit_surprise"]),
            snapshot_id=str(row.get("snapshot_id", "")),
            quality_score=float(row.get("quality_score", 1.0)),
            coverage_ratio=float(row.get("coverage_ratio", 1.0)),
            source_label=str(row.get("source_label", "")),
        )

    def history(self, region: str, as_of: date, lookback: int | None = None) -> list[MacroSnapshot]:
        rows = self._eligible_rows(region, as_of)
        if lookback is not None and lookback > 0:
            rows = rows.tail(int(lookback))
        return [
            MacroSnapshot(
                region=str(row.region),
                as_of=pd.Timestamp(row.date).date(),
                release_date=pd.Timestamp(row.release_date).date(),
                tradable_from=pd.Timestamp(row.tradable_from).date(),
                growth_surprise=float(row.growth_surprise),
                inflation_surprise=float(row.inflation_surprise),
                policy_surprise=float(row.policy_surprise),
                liquidity_surprise=float(row.liquidity_surprise),
                credit_surprise=float(row.credit_surprise),
                snapshot_id=str(getattr(row, "snapshot_id", "")),
                quality_score=float(getattr(row, "quality_score", 1.0)),
                coverage_ratio=float(getattr(row, "coverage_ratio", 1.0)),
                source_label=str(getattr(row, "source_label", "")),
            )
            for row in rows.itertuples()
        ]

    def regions(self) -> list[str]:
        return sorted(self._frame["region"].unique().tolist())


class CSVMacroSurpriseDataSource:
    """
    Point-in-time macro surprise events.

    Expected columns:
    region, factor, release_ts, tradable_from, surprise_z, source_label(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"region", "factor", "release_ts", "tradable_from", "surprise_z"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in macro surprise CSV: {sorted(missing)}")
        frame["region"] = frame["region"].astype(str).str.upper().str.strip()
        frame["factor"] = frame["factor"].astype(str).str.lower().str.strip()
        frame["release_ts"] = pd.to_datetime(frame["release_ts"], utc=True).dt.tz_localize(None)
        frame["tradable_from"] = pd.to_datetime(frame["tradable_from"], utc=True).dt.tz_localize(None)
        frame["surprise_z"] = pd.to_numeric(frame["surprise_z"], errors="coerce")
        frame["source_label"] = frame.get("source_label", pd.Series("", index=frame.index)).astype(str)
        frame = frame.dropna(subset=["release_ts", "tradable_from", "surprise_z"])
        self._frame = frame.sort_values(["region", "tradable_from", "factor"]).reset_index(drop=True)

    def events(self, region: str, as_of: date, lookback_days: int | None = None) -> list[MacroSurpriseEvent]:
        region = region.upper().strip()
        as_of_ts = pd.Timestamp(_to_date(as_of)) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        rows = self._frame[(self._frame["region"] == region) & (self._frame["tradable_from"] <= as_of_ts)]
        if rows.empty and region != "GLOBAL":
            rows = self._frame[(self._frame["region"] == "GLOBAL") & (self._frame["tradable_from"] <= as_of_ts)]
        if lookback_days is not None:
            cutoff = pd.Timestamp(_to_date(as_of)) - pd.Timedelta(days=int(lookback_days))
            rows = rows[rows["tradable_from"] >= cutoff]
        return [
            MacroSurpriseEvent(
                region=str(row.region),
                factor=str(row.factor),
                release_ts=pd.Timestamp(row.release_ts).to_pydatetime(),
                tradable_from=pd.Timestamp(row.tradable_from).to_pydatetime(),
                surprise_z=float(row.surprise_z),
                source_label=str(row.source_label),
            )
            for row in rows.itertuples()
        ]


class CSVVolatilityDataSource(VolatilityDataSource):
    """
    Expected columns:
    symbol, date, implied_vol(optional), implied_vol_percentile, event_risk_score, realized_vol(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "date", "implied_vol_percentile", "event_risk_score"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in volatility CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
        frame["implied_vol"] = pd.to_numeric(frame.get("implied_vol", np.nan), errors="coerce")
        frame["implied_vol_percentile"] = pd.to_numeric(
            frame["implied_vol_percentile"],
            errors="coerce",
        ).fillna(50.0)
        frame["event_risk_score"] = pd.to_numeric(frame["event_risk_score"], errors="coerce").fillna(0.0)
        frame["realized_vol"] = pd.to_numeric(frame.get("realized_vol", np.nan), errors="coerce")
        self._frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)

    def snapshot(self, symbol: str, as_of: date) -> VolatilitySnapshot | None:
        symbol = symbol.upper().strip()
        ts = pd.Timestamp(_to_date(as_of))
        rows = self._frame[(self._frame["symbol"] == symbol) & (self._frame["date"] <= ts)]
        if rows.empty:
            return None
        row = rows.iloc[-1]
        return VolatilitySnapshot(
            symbol=symbol,
            as_of=pd.Timestamp(row["date"]).date(),
            implied_vol=float(row["implied_vol"]) if pd.notna(row["implied_vol"]) else None,
            implied_vol_percentile=float(row["implied_vol_percentile"]),
            event_risk_score=float(row["event_risk_score"]),
            realized_vol=float(row["realized_vol"]) if pd.notna(row["realized_vol"]) else None,
        )


class CSVFuturesCurveDataSource(FuturesCurveDataSource):
    """
    Point-in-time futures curve snapshots.

    Expected columns:
    symbol, date, release_date, tradable_from,
    front_price(optional), second_price(optional), third_price(optional), fourth_price(optional),
    front_contract(optional), second_contract(optional), third_contract(optional), fourth_contract(optional),
    roll_yield_1m(optional), roll_yield_3m(optional), carry_score(optional),
    backwardation_score(optional), liquidity_score(optional), source_label(optional)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        frame = pd.read_csv(self.path)
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        required = {"symbol", "date", "release_date", "tradable_from"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing columns in futures curve CSV: {sorted(missing)}")

        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.date
        frame["release_date"] = pd.to_datetime(frame["release_date"], utc=True).dt.date
        frame["tradable_from"] = pd.to_datetime(frame["tradable_from"], utc=True).dt.date
        for col in ("front_price", "second_price", "third_price", "fourth_price"):
            frame[col] = pd.to_numeric(frame.get(col, np.nan), errors="coerce")
        for col in ("roll_yield_1m", "roll_yield_3m", "carry_score", "backwardation_score", "liquidity_score"):
            default = 1.0 if col == "liquidity_score" else 0.0
            frame[col] = pd.to_numeric(frame.get(col, default), errors="coerce").fillna(default)
        for col in ("front_contract", "second_contract", "third_contract", "fourth_contract", "source_label"):
            frame[col] = frame[col].astype(str) if col in frame.columns else ""

        missing_carry = frame["carry_score"].abs() <= 1e-12
        has_front_second = (
            frame["front_price"].notna()
            & frame["second_price"].notna()
            & (frame["second_price"].abs() > 1e-9)
        )
        inferred_1m = ((frame["front_price"] - frame["second_price"]) / frame["second_price"].abs()) * 12.0
        frame.loc[missing_carry & has_front_second, "carry_score"] = inferred_1m[missing_carry & has_front_second]
        missing_roll_1m = frame["roll_yield_1m"].abs() <= 1e-12
        frame.loc[missing_roll_1m & has_front_second, "roll_yield_1m"] = inferred_1m[
            missing_roll_1m & has_front_second
        ]
        missing_backwardation = frame["backwardation_score"].abs() <= 1e-12
        frame.loc[missing_backwardation & has_front_second, "backwardation_score"] = (
            (frame["front_price"] - frame["second_price"]) / frame["second_price"].abs()
        )[missing_backwardation & has_front_second]

        self._frame = frame.sort_values(["symbol", "tradable_from", "date"]).reset_index(drop=True)

    def snapshot(self, symbol: str, as_of: date) -> FuturesCurveSnapshot | None:
        symbol = symbol.upper().strip()
        d = _to_date(as_of)
        rows = self._frame[(self._frame["symbol"] == symbol) & (self._frame["tradable_from"] <= d)]
        if rows.empty:
            return None
        row = rows.iloc[-1]
        return FuturesCurveSnapshot(
            symbol=symbol,
            as_of=row["date"],
            release_date=row["release_date"],
            tradable_from=row["tradable_from"],
            front_price=float(row["front_price"]) if pd.notna(row["front_price"]) else None,
            second_price=float(row["second_price"]) if pd.notna(row["second_price"]) else None,
            third_price=float(row["third_price"]) if pd.notna(row["third_price"]) else None,
            fourth_price=float(row["fourth_price"]) if pd.notna(row["fourth_price"]) else None,
            front_contract=str(row["front_contract"]),
            second_contract=str(row["second_contract"]),
            third_contract=str(row["third_contract"]),
            fourth_contract=str(row["fourth_contract"]),
            roll_yield_1m=float(row["roll_yield_1m"]),
            roll_yield_3m=float(row["roll_yield_3m"]),
            carry_score=float(row["carry_score"]),
            backwardation_score=float(row["backwardation_score"]),
            liquidity_score=float(row["liquidity_score"]),
            source_label=str(row["source_label"]),
        )
