from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
import pandas as pd
from quantbt.altdata.http import open_url


FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
SUPPORTED_PROVIDERS = {"LOCAL_CSV", "CSV_URL", "FRED_API", "ALFRED_API"}


@dataclass(slots=True)
class PublicMacroSeriesSpec:
    region: str
    factor: str
    provider: str
    series: str
    source: str
    url: str = ""
    path: str = ""
    date_col: str = "date"
    value_col: str = "value"
    release_date_col: str = ""
    tradable_from_col: str = ""
    transform: str = "diff_1"
    weight: float = 1.0
    scale: float = 1.0
    release_lag_days: int = 0
    publication_lag_business_days: int = 5
    observation_start: str = ""
    observation_end: str = ""
    realtime_start: str = ""
    realtime_end: str = ""
    units: str = ""
    frequency: str = ""
    aggregation_method: str = ""
    output_type: int = 1

    @property
    def cache_stem(self) -> str:
        raw = f"{self.provider}_{self.region}_{self.factor}_{self.series}"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)


def _to_frame_from_csv_bytes(payload: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(payload))


def load_public_macro_series_specs(path: str | Path) -> list[PublicMacroSeriesSpec]:
    frame = pd.read_csv(path)
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    required = {"region", "factor", "provider", "series", "source"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns in macro public series config: {sorted(missing)}")

    specs: list[PublicMacroSeriesSpec] = []
    for row in frame.fillna("").itertuples(index=False):
        values = row._asdict()
        provider = str(values["provider"]).upper().strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported macro provider: {provider}")
        specs.append(
            PublicMacroSeriesSpec(
                region=str(values["region"]).upper().strip(),
                factor=str(values["factor"]).lower().strip(),
                provider=provider,
                series=str(values["series"]).upper().strip(),
                source=str(values["source"]).strip(),
                url=str(values.get("url", "")).strip(),
                path=str(values.get("path", "")).strip(),
                date_col=str(values.get("date_col", "date") or "date").strip(),
                value_col=str(values.get("value_col", "value") or "value").strip(),
                release_date_col=str(values.get("release_date_col", "")).strip(),
                tradable_from_col=str(values.get("tradable_from_col", "")).strip(),
                transform=str(values.get("transform", "diff_1") or "diff_1").strip().lower(),
                weight=float(values.get("weight", 1.0) or 1.0),
                scale=float(values.get("scale", 1.0) or 1.0),
                release_lag_days=int(values.get("release_lag_days", 0) or 0),
                publication_lag_business_days=int(values.get("publication_lag_business_days", 5) or 5),
                observation_start=str(values.get("observation_start", "")).strip(),
                observation_end=str(values.get("observation_end", "")).strip(),
                realtime_start=str(values.get("realtime_start", "")).strip(),
                realtime_end=str(values.get("realtime_end", "")).strip(),
                units=str(values.get("units", "")).strip(),
                frequency=str(values.get("frequency", "")).strip(),
                aggregation_method=str(values.get("aggregation_method", "")).strip(),
                output_type=int(values.get("output_type", 1) or 1),
            )
        )
    return specs


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def _read_csv_from_source(spec: PublicMacroSeriesSpec, cache_dir: Path, *, refresh: bool) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{spec.cache_stem}.csv"
    if spec.provider == "LOCAL_CSV":
        local_path = Path(spec.path or spec.url).expanduser()
        return pd.read_csv(local_path)
    if not refresh and cache_path.exists():
        return pd.read_csv(cache_path)
    if spec.provider == "CSV_URL":
        if not spec.url:
            raise ValueError(f"CSV_URL provider requires url for {spec.series}")
        with open_url(spec.url, timeout=30) as response:
            payload = response.read()
        cache_path.write_bytes(payload)
        return _to_frame_from_csv_bytes(payload)
    if spec.provider in {"FRED_API", "ALFRED_API"}:
        raise AssertionError(f"{spec.provider} handled separately")
    raise ValueError(f"Unsupported provider: {spec.provider}")


def _fred_api_query(spec: PublicMacroSeriesSpec, api_key: str) -> str:
    query: dict[str, str | int] = {
        "series_id": spec.series,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }
    if spec.provider == "ALFRED_API":
        query["realtime_start"] = spec.realtime_start or "1776-07-04"
        query["realtime_end"] = spec.realtime_end or "9999-12-31"
        query["output_type"] = int(spec.output_type or 4)
    if spec.observation_start:
        query["observation_start"] = spec.observation_start
    if spec.observation_end:
        query["observation_end"] = spec.observation_end
    if spec.units:
        query["units"] = spec.units
    if spec.frequency:
        query["frequency"] = spec.frequency
    if spec.aggregation_method:
        query["aggregation_method"] = spec.aggregation_method
    return urlencode(query)


def _read_fred_api_series(
    spec: PublicMacroSeriesSpec,
    cache_dir: Path,
    *,
    refresh: bool,
    fred_api_key: str | None,
) -> pd.DataFrame:
    api_key = (fred_api_key or "").strip()
    if not api_key:
        raise ValueError(
            f"{spec.provider} provider requires an API key. Set FRED_API_KEY or pass --fred-api-key."
        )
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{spec.cache_stem}.json"
    if not refresh and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        query = _fred_api_query(spec, api_key)
        with open_url(f"{FRED_OBSERVATIONS_URL}?{query}", timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    observations = payload.get("observations", [])
    frame = pd.DataFrame(observations)
    if frame.empty:
        return pd.DataFrame(columns=["date", "value", "realtime_start"])
    return frame


def fetch_public_macro_series(
    spec: PublicMacroSeriesSpec,
    *,
    cache_dir: str | Path = ".cache/alt_data/macro_series",
    refresh: bool = False,
    fred_api_key: str | None = None,
) -> pd.DataFrame:
    cache = Path(cache_dir)
    if spec.provider in {"FRED_API", "ALFRED_API"}:
        frame = _read_fred_api_series(spec, cache, refresh=refresh, fred_api_key=fred_api_key)
    else:
        frame = _read_csv_from_source(spec, cache, refresh=refresh)
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    return frame


def normalize_public_macro_series_frame(
    frame: pd.DataFrame,
    spec: PublicMacroSeriesSpec,
) -> pd.DataFrame:
    working = frame.copy()
    working.columns = [str(col).strip().lower() for col in working.columns]
    date_col = spec.date_col.lower()
    value_col = spec.value_col.lower()
    if date_col not in working.columns:
        raise ValueError(f"Missing date column '{spec.date_col}' for {spec.series}")
    if value_col not in working.columns:
        if spec.provider in {"FRED_API", "ALFRED_API"} and "value" in working.columns:
            value_col = "value"
        else:
            raise ValueError(f"Missing value column '{spec.value_col}' for {spec.series}")

    out = pd.DataFrame()
    out["date"] = _parse_dates(working[date_col])
    out["value"] = pd.to_numeric(working[value_col], errors="coerce") * float(spec.scale)

    release_col = spec.release_date_col.lower().strip()
    tradable_col = spec.tradable_from_col.lower().strip()
    if release_col and release_col in working.columns:
        out["release_date"] = _parse_dates(working[release_col])
    elif spec.provider == "ALFRED_API" and "realtime_start" in working.columns:
        out["release_date"] = _parse_dates(working["realtime_start"])
    elif "release_date" in working.columns:
        out["release_date"] = _parse_dates(working["release_date"])
    else:
        out["release_date"] = out["date"] + pd.to_timedelta(int(spec.release_lag_days), unit="D")

    if tradable_col and tradable_col in working.columns:
        out["tradable_from"] = _parse_dates(working[tradable_col])
    elif "tradable_from" in working.columns:
        out["tradable_from"] = _parse_dates(working["tradable_from"])
    else:
        out["tradable_from"] = out["release_date"] + pd.offsets.BDay(max(int(spec.publication_lag_business_days), 0))

    out = out.dropna(subset=["date", "value", "release_date", "tradable_from"]).copy()
    out["region"] = spec.region
    out["factor"] = spec.factor
    out["series"] = spec.series
    out["source"] = spec.source
    out["transform"] = spec.transform
    out["weight"] = float(spec.weight)
    out = out.sort_values(["date", "release_date", "tradable_from"]).reset_index(drop=True)
    out = out.drop_duplicates(subset=["date", "release_date", "series"], keep="first")
    return out[
        [
            "region",
            "factor",
            "date",
            "release_date",
            "tradable_from",
            "value",
            "series",
            "source",
            "transform",
            "weight",
        ]
    ]


def build_public_macro_events_from_config(
    config_csv: str | Path,
    *,
    cache_dir: str | Path = ".cache/alt_data/macro_series",
    refresh: bool = False,
    fred_api_key: str | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for spec in load_public_macro_series_specs(config_csv):
        raw = fetch_public_macro_series(
            spec,
            cache_dir=cache_dir,
            refresh=refresh,
            fred_api_key=fred_api_key,
        )
        normalized = normalize_public_macro_series_frame(raw, spec)
        if not normalized.empty:
            frames.append(normalized)
    if not frames:
        return pd.DataFrame(
            columns=[
                "region",
                "factor",
                "date",
                "release_date",
                "tradable_from",
                "value",
                "series",
                "source",
                "transform",
                "weight",
            ]
        )
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["region", "factor", "tradable_from", "date", "series"]).reset_index(drop=True)
    return out


def write_public_macro_events_csv(
    config_csv: str | Path,
    output_path: str | Path,
    *,
    cache_dir: str | Path = ".cache/alt_data/macro_series",
    refresh: bool = False,
    fred_api_key: str | None = None,
) -> Path:
    output = Path(output_path)
    frame = build_public_macro_events_from_config(
        config_csv,
        cache_dir=cache_dir,
        refresh=refresh,
        fred_api_key=fred_api_key,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    serializable = frame.copy()
    for column in ["date", "release_date", "tradable_from"]:
        serializable[column] = pd.to_datetime(serializable[column]).dt.date.astype(str)
    serializable.to_csv(output, index=False)
    return output
