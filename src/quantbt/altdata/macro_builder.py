from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


FACTOR_COLUMNS = ("growth", "inflation", "policy", "liquidity", "credit")
SUPPORTED_TRANSFORMS = {
    "level",
    "diff_1",
    "diff_12",
    "pct_change_1",
    "pct_change_12",
    "zscore",
}
DEFAULT_GLOBAL_WEIGHTS = {
    "US": 0.40,
    "EUROPE": 0.25,
    "JAPAN": 0.15,
    "EM": 0.20,
}


@dataclass(slots=True)
class PublicMacroBuilderConfig:
    z_window: int = 36
    min_observations: int = 12
    publication_lag_business_days: int = 5
    freshness_half_life_days: int = 45
    clip_zscore: float = 3.0
    global_weights: dict[str, float] | None = None

    def resolved_global_weights(self) -> dict[str, float]:
        return dict(self.global_weights or DEFAULT_GLOBAL_WEIGHTS)


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def load_macro_factor_events(
    path: str | Path,
    *,
    publication_lag_business_days: int = 5,
) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    required = {"region", "factor", "date", "release_date", "value"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns in macro factor events CSV: {sorted(missing)}")

    frame["region"] = frame["region"].astype(str).str.upper().str.strip()
    frame["factor"] = frame["factor"].astype(str).str.lower().str.strip()
    invalid_factors = sorted(set(frame[~frame["factor"].isin(FACTOR_COLUMNS)]["factor"]))
    if invalid_factors:
        raise ValueError(f"Unsupported macro factors: {invalid_factors}")

    frame["date"] = _parse_dates(frame["date"])
    frame["release_date"] = _parse_dates(frame["release_date"])
    default_tradable = frame["release_date"] + pd.offsets.BDay(max(int(publication_lag_business_days), 0))
    frame["tradable_from"] = _parse_dates(frame.get("tradable_from", default_tradable))
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["series"] = frame.get("series", frame["factor"]).astype(str).str.upper().str.strip()
    frame["source"] = frame.get("source", frame["series"]).astype(str).str.strip()
    frame["transform"] = frame.get("transform", pd.Series("diff_1", index=frame.index)).astype(str).str.lower().str.strip()
    frame["weight"] = pd.to_numeric(
        frame.get("weight", pd.Series(1.0, index=frame.index)),
        errors="coerce",
    ).fillna(1.0)
    invalid_transforms = sorted(set(frame[~frame["transform"].isin(SUPPORTED_TRANSFORMS)]["transform"]))
    if invalid_transforms:
        raise ValueError(f"Unsupported transforms: {invalid_transforms}")

    frame = frame.dropna(subset=["date", "release_date", "tradable_from", "value"])
    frame = frame.sort_values(["region", "factor", "series", "date", "release_date", "tradable_from"])
    frame = frame.drop_duplicates(subset=["region", "factor", "series", "date"], keep="first")
    return frame.reset_index(drop=True)


def _apply_transform(values: np.ndarray, transform: str) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if transform == "zscore":
        return values.astype(float, copy=True)
    if transform == "level":
        return values.astype(float, copy=True)
    if transform == "diff_1":
        if len(values) >= 2:
            out[1:] = values[1:] - values[:-1]
        return out
    if transform == "diff_12":
        if len(values) >= 13:
            out[12:] = values[12:] - values[:-12]
        return out
    if transform == "pct_change_1":
        if len(values) >= 2:
            prev = values[:-1]
            valid = prev != 0.0
            temp = np.full(len(values) - 1, np.nan, dtype=float)
            temp[valid] = values[1:][valid] / prev[valid] - 1.0
            out[1:] = temp
        return out
    if transform == "pct_change_12":
        if len(values) >= 13:
            prev = values[:-12]
            valid = prev != 0.0
            temp = np.full(len(values) - 12, np.nan, dtype=float)
            temp[valid] = values[12:][valid] / prev[valid] - 1.0
            out[12:] = temp
        return out
    raise ValueError(f"Unsupported transform: {transform}")


def _surprise_from_history(
    transformed: np.ndarray,
    *,
    z_window: int,
    min_observations: int,
    clip_zscore: float,
) -> np.ndarray:
    surprises = np.full(len(transformed), np.nan, dtype=float)
    for idx, current in enumerate(transformed):
        if not np.isfinite(current):
            continue
        prior = transformed[max(0, idx - z_window) : idx]
        prior = prior[np.isfinite(prior)]
        if len(prior) < min_observations:
            continue
        mean = float(np.mean(prior))
        std = float(np.std(prior, ddof=0))
        median = float(np.median(prior))
        mad = float(np.median(np.abs(prior - median)))
        robust_std = 1.4826 * mad if mad > 0 else 0.0
        std_used = max(std, robust_std * 0.5, 1e-6)
        surprises[idx] = float(np.clip((current - mean) / std_used, -clip_zscore, clip_zscore))
    return surprises


def _compute_series_surprises(frame: pd.DataFrame, config: PublicMacroBuilderConfig) -> pd.DataFrame:
    enriched: list[pd.DataFrame] = []
    for (_, _, _, transform), group in frame.groupby(
        ["region", "factor", "series", "transform"],
        sort=False,
    ):
        group = group.sort_values(["date", "release_date", "tradable_from"]).copy()
        values = group["value"].to_numpy(dtype=float)
        transformed = _apply_transform(values, transform)
        if transform == "zscore":
            surprises = np.clip(values, -config.clip_zscore, config.clip_zscore)
        else:
            surprises = _surprise_from_history(
                transformed,
                z_window=config.z_window,
                min_observations=config.min_observations,
                clip_zscore=config.clip_zscore,
            )
        group["transformed_value"] = transformed
        group["surprise"] = surprises
        enriched.append(group)
    if not enriched:
        return frame.iloc[0:0].copy()
    return pd.concat(enriched, ignore_index=True)


def _freshness_weight(days_stale: int, half_life_days: int) -> float:
    if half_life_days <= 0:
        return 1.0
    return float(0.5 ** (max(days_stale, 0) / half_life_days))


def _aggregate_factor_state(
    *,
    active_rows: list[pd.Series],
    event_date: pd.Timestamp,
    config: PublicMacroBuilderConfig,
) -> tuple[float, float, str]:
    if not active_rows:
        return (0.0, 0.0, "")
    weights: list[float] = []
    values: list[float] = []
    sources: list[str] = []
    freshness_scores: list[float] = []
    for row in active_rows:
        surprise = float(row["surprise"])
        if not np.isfinite(surprise):
            continue
        days_stale = int((event_date - pd.Timestamp(row["tradable_from"])).days)
        freshness = _freshness_weight(days_stale, config.freshness_half_life_days)
        eff_weight = float(row["weight"]) * freshness
        if eff_weight <= 0:
            continue
        weights.append(eff_weight)
        values.append(surprise)
        freshness_scores.append(freshness)
        source = str(row.get("source", "")).strip()
        if source:
            sources.append(source)
    if not weights:
        return (0.0, 0.0, "")
    factor_value = float(np.average(values, weights=weights))
    freshness_score = float(np.average(freshness_scores, weights=weights))
    source_label = "|".join(sorted(set(sources)))
    return (factor_value, freshness_score, source_label)


def _build_region_snapshots(
    events: pd.DataFrame,
    config: PublicMacroBuilderConfig,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for region, region_events in events.groupby("region", sort=True):
        region_events = region_events.sort_values(["tradable_from", "release_date", "factor", "series"])
        active: dict[tuple[str, str], pd.Series] = {}
        event_counter = 0
        for event_ts, today_rows in region_events.groupby("tradable_from", sort=True):
            for _, row in today_rows.iterrows():
                active[(str(row["factor"]), str(row["series"]))] = row

            factor_values = {factor: 0.0 for factor in FACTOR_COLUMNS}
            factor_freshness: list[float] = []
            coverage_count = 0
            source_parts: list[str] = []
            for factor in FACTOR_COLUMNS:
                active_rows = [row for (active_factor, _), row in active.items() if active_factor == factor]
                value, freshness, source_label = _aggregate_factor_state(
                    active_rows=active_rows,
                    event_date=pd.Timestamp(event_ts),
                    config=config,
                )
                factor_values[factor] = value
                if freshness > 0.0:
                    coverage_count += 1
                    factor_freshness.append(freshness)
                    if source_label:
                        source_parts.append(f"{factor}:{source_label}")

            if coverage_count == 0:
                continue

            event_counter += 1
            coverage_ratio = coverage_count / float(len(FACTOR_COLUMNS))
            freshness_score = float(np.mean(factor_freshness)) if factor_freshness else 0.0
            quality_score = float(np.clip(coverage_ratio * freshness_score, 0.0, 1.0))
            release_date = pd.Timestamp(today_rows["release_date"].max())
            rows.append(
                {
                    "date": pd.Timestamp(event_ts).date().isoformat(),
                    "region": region,
                    "release_date": release_date.date().isoformat(),
                    "tradable_from": pd.Timestamp(event_ts).date().isoformat(),
                    "growth_surprise": round(float(factor_values["growth"]), 6),
                    "inflation_surprise": round(float(factor_values["inflation"]), 6),
                    "policy_surprise": round(float(factor_values["policy"]), 6),
                    "liquidity_surprise": round(float(factor_values["liquidity"]), 6),
                    "credit_surprise": round(float(factor_values["credit"]), 6),
                    "snapshot_id": f"{region}_{pd.Timestamp(event_ts).strftime('%Y%m%d')}_{event_counter:04d}",
                    "quality_score": round(quality_score, 6),
                    "coverage_ratio": round(coverage_ratio, 6),
                    "source_label": ";".join(source_parts),
                }
            )
    return pd.DataFrame(rows)


def _build_global_snapshots(region_snapshots: pd.DataFrame, config: PublicMacroBuilderConfig) -> pd.DataFrame:
    if region_snapshots.empty:
        return region_snapshots.copy()
    weights = config.resolved_global_weights()
    base = region_snapshots[region_snapshots["region"].isin(weights)].copy()
    if base.empty:
        return region_snapshots.iloc[0:0].copy()

    base["tradable_from"] = pd.to_datetime(base["tradable_from"], utc=True).dt.tz_localize(None)
    base["release_date"] = pd.to_datetime(base["release_date"], utc=True).dt.tz_localize(None)
    base = base.sort_values(["region", "tradable_from", "snapshot_id"]).reset_index(drop=True)

    rows: list[dict[str, object]] = []
    event_dates = sorted(base["tradable_from"].drop_duplicates().tolist())
    for idx, event_ts in enumerate(event_dates, start=1):
        available: list[pd.Series] = []
        for region in weights:
            rows_region = base[(base["region"] == region) & (base["tradable_from"] <= event_ts)]
            if rows_region.empty:
                continue
            available.append(rows_region.iloc[-1])
        if not available:
            continue

        eff_weights: list[float] = []
        for row in available:
            base_weight = float(weights.get(str(row["region"]), 0.0))
            quality = float(row.get("quality_score", 1.0))
            eff_weights.append(base_weight * max(quality, 1e-6))
        weight_sum = float(sum(eff_weights))
        if weight_sum <= 0:
            continue

        factor_values: dict[str, float] = {}
        for factor in FACTOR_COLUMNS:
            column = f"{factor}_surprise"
            factor_values[column] = round(
                float(np.average([float(row[column]) for row in available], weights=eff_weights)),
                6,
            )
        release_date = max(pd.Timestamp(row["release_date"]) for row in available)
        coverage_ratio = float(np.average([float(row["coverage_ratio"]) for row in available], weights=eff_weights))
        quality_score = float(np.average([float(row["quality_score"]) for row in available], weights=eff_weights))
        source_label = ";".join(sorted({str(row["region"]) for row in available}))
        rows.append(
            {
                "date": pd.Timestamp(event_ts).date().isoformat(),
                "region": "GLOBAL",
                "release_date": release_date.date().isoformat(),
                "tradable_from": pd.Timestamp(event_ts).date().isoformat(),
                **factor_values,
                "snapshot_id": f"GLOBAL_{pd.Timestamp(event_ts).strftime('%Y%m%d')}_{idx:04d}",
                "quality_score": round(quality_score, 6),
                "coverage_ratio": round(coverage_ratio, 6),
                "source_label": source_label,
            }
        )
    return pd.DataFrame(rows)


def build_public_macro_snapshot_frame(
    events_csv: str | Path,
    *,
    config: PublicMacroBuilderConfig | None = None,
) -> pd.DataFrame:
    resolved = config or PublicMacroBuilderConfig()
    events = load_macro_factor_events(
        events_csv,
        publication_lag_business_days=resolved.publication_lag_business_days,
    )
    enriched = _compute_series_surprises(events, resolved)
    region_snapshots = _build_region_snapshots(enriched, resolved)
    global_snapshots = _build_global_snapshots(region_snapshots, resolved)
    out = pd.concat([region_snapshots, global_snapshots], ignore_index=True)
    if out.empty:
        return out
    out = out.sort_values(["region", "tradable_from", "snapshot_id"]).reset_index(drop=True)
    return out[
        [
            "date",
            "region",
            "release_date",
            "tradable_from",
            "growth_surprise",
            "inflation_surprise",
            "policy_surprise",
            "liquidity_surprise",
            "credit_surprise",
            "snapshot_id",
            "quality_score",
            "coverage_ratio",
            "source_label",
        ]
    ]


def build_macro_surprise_event_frame(
    events_csv: str | Path,
    *,
    config: PublicMacroBuilderConfig | None = None,
) -> pd.DataFrame:
    """
    Build a point-in-time public macro surprise proxy from raw factor events.

    This is not an economist-consensus surprise. It is a statistical first-release
    innovation computed with the same transform/z-score discipline as the public
    macro regime builder, then timestamped by release/tradable time.
    """

    resolved = config or PublicMacroBuilderConfig()
    events = load_macro_factor_events(
        events_csv,
        publication_lag_business_days=resolved.publication_lag_business_days,
    )
    enriched = _compute_series_surprises(events, resolved)
    columns = ["region", "factor", "release_ts", "tradable_from", "surprise_z", "source_label"]
    if enriched.empty:
        return pd.DataFrame(columns=columns)
    enriched = enriched[np.isfinite(enriched["surprise"])].copy()
    if enriched.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    group_cols = ["region", "factor", "release_date", "tradable_from"]
    for (region, factor, release_date, tradable_from), group in enriched.groupby(group_cols, sort=True):
        weights = group["weight"].astype(float).clip(lower=0.0).to_numpy(dtype=float)
        values = group["surprise"].to_numpy(dtype=float)
        if float(np.sum(weights)) <= 0.0:
            weights = np.ones(len(values), dtype=float)
        source_parts: list[str] = []
        for row in group.itertuples():
            source = str(getattr(row, "source", "")).strip()
            series = str(getattr(row, "series", "")).strip()
            source_parts.append(source or series)
        rows.append(
            {
                "region": str(region),
                "factor": str(factor),
                "release_ts": pd.Timestamp(release_date).isoformat(),
                "tradable_from": pd.Timestamp(tradable_from).isoformat(),
                "surprise_z": round(float(np.average(values, weights=weights)), 6),
                "source_label": "|".join(sorted({part for part in source_parts if part})),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["tradable_from", "region", "factor"]).reset_index(drop=True)


def write_macro_surprise_event_csv(
    events_csv: str | Path,
    output_path: str | Path,
    *,
    config: PublicMacroBuilderConfig | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = build_macro_surprise_event_frame(events_csv, config=config)
    frame.to_csv(output, index=False)
    return output


def write_public_macro_snapshot_csv(
    events_csv: str | Path,
    output_path: str | Path,
    *,
    config: PublicMacroBuilderConfig | None = None,
) -> Path:
    output = Path(output_path)
    frame = build_public_macro_snapshot_frame(events_csv, config=config)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return output
