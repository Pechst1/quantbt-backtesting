from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from pathlib import Path
import pandas as pd

from quantbt.altdata.base import CorporateActionsDataSource, EarningsDataSource, FactorDataSource
from quantbt.altdata.http import open_url
from quantbt.altdata.models import CorporateAction, EarningsAnnouncement

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional dependency
    yf = None


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


class YahooEarningsDataSource(EarningsDataSource):
    """
    Free proxy source for earnings surprises.
    Yahoo does not publish analyst estimate standard deviations directly, so this source
    uses a conservative proxy based on estimate magnitude.
    """

    def __init__(
        self,
        symbols: list[str],
        estimate_std_proxy_pct: float = 0.10,
        min_estimate_std: float = 0.01,
    ) -> None:
        if yf is None:
            raise RuntimeError("yfinance is required for YahooEarningsDataSource.")
        self.estimate_std_proxy_pct = estimate_std_proxy_pct
        self.min_estimate_std = min_estimate_std
        self._events: list[EarningsAnnouncement] = []
        self._events_by_date: dict[date, list[EarningsAnnouncement]] = {}
        self._events_by_symbol: dict[str, list[EarningsAnnouncement]] = {}
        self._load(symbols)

    def _load(self, symbols: list[str]) -> None:
        rows: list[EarningsAnnouncement] = []
        for symbol in symbols:
            ticker = yf.Ticker(symbol)
            try:
                frame = ticker.get_earnings_dates(limit=80)
            except Exception:
                continue
            if frame is None or frame.empty:
                continue
            working = frame.copy()
            working.columns = [str(col).strip().lower().replace(" ", "_") for col in working.columns]
            working.index = pd.to_datetime(working.index, utc=True).tz_localize(None)
            if "eps_estimate" not in working.columns or "reported_eps" not in working.columns:
                continue

            working = working.dropna(subset=["eps_estimate", "reported_eps"])
            working = working.sort_index()
            for idx, row in working.iterrows():
                estimate = float(row["eps_estimate"])
                actual = float(row["reported_eps"])
                std_proxy = max(abs(estimate) * self.estimate_std_proxy_pct, self.min_estimate_std)
                rows.append(
                    EarningsAnnouncement(
                        symbol=symbol.upper(),
                        announcement_ts=idx.to_pydatetime(),
                        eps_estimate=estimate,
                        eps_actual=actual,
                        estimate_std=std_proxy,
                    )
                )

        rows = sorted(rows, key=lambda item: item.announcement_ts)
        for event in rows:
            self._events_by_date.setdefault(event.announcement_ts.date(), []).append(event)
            self._events_by_symbol.setdefault(event.symbol, []).append(event)
        for symbol, events in self._events_by_symbol.items():
            for i, event in enumerate(events):
                if i + 1 < len(events):
                    event.next_announcement_ts = events[i + 1].announcement_ts
        self._events = rows

    def announcements_on(self, as_of: date) -> list[EarningsAnnouncement]:
        return list(self._events_by_date.get(_to_date(as_of), []))

    def next_announcement_after(self, symbol: str, as_of: date) -> datetime | None:
        rows = self._events_by_symbol.get(symbol.upper(), [])
        d = _to_date(as_of)
        for event in rows:
            if event.announcement_ts.date() > d:
                return event.announcement_ts
        return None


class YahooCorporateActionsDataSource(CorporateActionsDataSource):
    """Free proxy source for dividends/splits corporate action filtering."""

    def __init__(self, symbols: list[str]) -> None:
        if yf is None:
            raise RuntimeError("yfinance is required for YahooCorporateActionsDataSource.")
        self._actions: dict[tuple[str, date], list[CorporateAction]] = {}
        self._load(symbols)

    def _load(self, symbols: list[str]) -> None:
        for symbol in symbols:
            ticker = yf.Ticker(symbol)
            try:
                actions = ticker.actions
            except Exception:
                continue
            if actions is None or actions.empty:
                continue
            actions = actions.copy()
            actions.index = pd.to_datetime(actions.index, utc=True).tz_localize(None)
            for idx, row in actions.iterrows():
                d = idx.date()
                if "Dividends" in actions.columns and float(row.get("Dividends", 0.0)) != 0.0:
                    self._actions.setdefault((symbol.upper(), d), []).append(
                        CorporateAction(
                            symbol=symbol.upper(),
                            action_date=d,
                            action_type="DIVIDEND",
                            value=float(row["Dividends"]),
                        )
                    )
                if "Stock Splits" in actions.columns and float(row.get("Stock Splits", 0.0)) != 0.0:
                    self._actions.setdefault((symbol.upper(), d), []).append(
                        CorporateAction(
                            symbol=symbol.upper(),
                            action_date=d,
                            action_type="SPLIT",
                            value=float(row["Stock Splits"]),
                        )
                    )

    def actions_on(self, symbol: str, as_of: date) -> list[CorporateAction]:
        return list(self._actions.get((symbol.upper(), _to_date(as_of)), []))


class KenFrenchFactorDataSource(FactorDataSource):
    """
    Free Fama-French 3-factor + RF source (daily) from Dartmouth.
    Data is cached locally to avoid repeated downloads.
    """

    URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"

    def __init__(self, cache_file: str | Path = ".cache/alt_data/ff3_daily.parquet") -> None:
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._frame = self._load()

    def _parse_ken_french_zip(self, payload: bytes) -> pd.DataFrame:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        member = archive.namelist()[0]
        content = archive.read(member).decode("utf-8", errors="ignore").splitlines()

        rows: list[tuple[str, float, float, float, float]] = []
        started = False
        for line in content:
            if "Mkt-RF" in line and "SMB" in line and "HML" in line and "RF" in line:
                started = True
                continue
            if not started:
                continue
            parts = [item.strip() for item in line.split(",")]
            if len(parts) < 5:
                continue
            key = parts[0]
            if not key.isdigit():
                break
            if len(key) != 8:
                continue
            try:
                rows.append(
                    (
                        key,
                        float(parts[1]) / 100.0,
                        float(parts[2]) / 100.0,
                        float(parts[3]) / 100.0,
                        float(parts[4]) / 100.0,
                    )
                )
            except ValueError:
                continue

        frame = pd.DataFrame(rows, columns=["date", "market_excess", "smb", "hml", "rf"])
        frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
        frame = frame.set_index("date").sort_index()
        return frame

    def _load(self) -> pd.DataFrame:
        if self.cache_file.exists():
            frame = pd.read_parquet(self.cache_file)
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            return frame[["market_excess", "smb", "hml", "rf"]]

        with open_url(self.URL, timeout=30) as response:
            payload = response.read()
        frame = self._parse_ken_french_zip(payload)
        frame.to_parquet(self.cache_file)
        return frame

    def factor_window(self, dates: pd.Index) -> pd.DataFrame:
        idx = pd.to_datetime(dates).tz_localize(None)
        factors = self._frame.reindex(idx).ffill().fillna(0.0)
        factors.index = idx
        return factors
