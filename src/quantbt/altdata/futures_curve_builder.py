from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT_COLUMNS = [
    "symbol",
    "date",
    "release_date",
    "tradable_from",
    "front_contract",
    "second_contract",
    "third_contract",
    "fourth_contract",
    "front_price",
    "second_price",
    "third_price",
    "fourth_price",
    "roll_yield_1m",
    "roll_yield_3m",
    "carry_score",
    "backwardation_score",
    "liquidity_score",
    "source_label",
]


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def _normalize_ranked_contract_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "contract_rank", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns in ranked futures curve input: {sorted(missing)}")

    working = frame.copy()
    working["symbol"] = working["symbol"].astype(str).str.upper().str.strip()
    working["date"] = _parse_dates(working["date"])
    working["contract_rank"] = pd.to_numeric(working["contract_rank"], errors="coerce").astype("Int64")
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    working = working[working["contract_rank"].isin([1, 2, 3, 4])]
    working = working.dropna(subset=["symbol", "date", "contract_rank", "close"]).copy()
    if working.empty:
        return pd.DataFrame(columns=["symbol", "date", "front_price", "second_price", "third_price"])

    prices = working.pivot_table(
        index=["symbol", "date"],
        columns="contract_rank",
        values="close",
        aggfunc="last",
    ).rename(columns={1: "front_price", 2: "second_price", 3: "third_price", 4: "fourth_price"})
    out = prices.reset_index()
    for col in ("third_price", "fourth_price"):
        if col not in out.columns:
            out[col] = np.nan

    contract_col = "contract" if "contract" in working.columns else "contract_symbol" if "contract_symbol" in working.columns else ""
    if contract_col:
        labels = working.pivot_table(
            index=["symbol", "date"],
            columns="contract_rank",
            values=contract_col,
            aggfunc="last",
        ).rename(
            columns={1: "front_contract", 2: "second_contract", 3: "third_contract", 4: "fourth_contract"}
        )
        out = out.merge(labels.reset_index(), on=["symbol", "date"], how="left")

    for col in ("front_contract", "second_contract", "third_contract", "fourth_contract"):
        if col not in out.columns:
            out[col] = ""
    return out


def _normalize_wide_curve_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "front_price", "second_price"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns in wide futures curve input: {sorted(missing)}")
    out = frame.copy()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["date"] = _parse_dates(out["date"])
    for col in ("front_price", "second_price", "third_price", "fourth_price"):
        out[col] = pd.to_numeric(out.get(col, np.nan), errors="coerce")
    for col in ("front_contract", "second_contract", "third_contract", "fourth_contract"):
        out[col] = out[col].astype(str) if col in out.columns else ""
    return out


def _normalize_curve_input(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    working.columns = [str(col).strip().lower() for col in working.columns]
    if {"contract_rank", "close"}.issubset(working.columns):
        return _normalize_ranked_contract_frame(working)
    return _normalize_wide_curve_frame(working)


def build_futures_curve_signal_frame(
    input_csv: str | Path,
    *,
    publication_lag_business_days: int = 1,
    source_label: str = "LOCAL_CSV",
    clip_carry: float = 1.0,
) -> pd.DataFrame:
    raw = pd.read_csv(input_csv)
    raw.columns = [str(col).strip().lower() for col in raw.columns]
    base = _normalize_curve_input(raw)
    if base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    base = base.dropna(subset=["symbol", "date", "front_price", "second_price"]).copy()
    base = base[base["second_price"].abs() > 1e-9]
    if base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    raw_release = raw.get("release_date")
    raw_tradable = raw.get("tradable_from")
    if raw_release is not None and len(raw_release) == len(base):
        base["release_date"] = _parse_dates(raw_release)
    else:
        base["release_date"] = base["date"]
    if raw_tradable is not None and len(raw_tradable) == len(base):
        base["tradable_from"] = _parse_dates(raw_tradable)
    else:
        base["tradable_from"] = base["release_date"] + pd.offsets.BDay(max(int(publication_lag_business_days), 0))

    base["roll_yield_1m"] = (
        (base["front_price"] - base["second_price"]) / base["second_price"].abs()
    ) * 12.0
    has_third = base["third_price"].notna() & (base["third_price"].abs() > 1e-9)
    has_fourth = base["fourth_price"].notna() & (base["fourth_price"].abs() > 1e-9)
    base["roll_yield_3m"] = base["roll_yield_1m"]
    base.loc[has_third, "roll_yield_3m"] = (
        (base.loc[has_third, "front_price"] - base.loc[has_third, "third_price"])
        / base.loc[has_third, "third_price"].abs()
    ) * 6.0
    base.loc[has_fourth, "roll_yield_3m"] = (
        (base.loc[has_fourth, "front_price"] - base.loc[has_fourth, "fourth_price"])
        / base.loc[has_fourth, "fourth_price"].abs()
    ) * 4.0
    base["backwardation_score"] = (
        (base["front_price"] - base["second_price"]) / base["second_price"].abs()
    )
    base["carry_score"] = 0.70 * base["roll_yield_1m"] + 0.30 * base["roll_yield_3m"]
    if clip_carry > 0:
        base["roll_yield_1m"] = base["roll_yield_1m"].clip(-clip_carry, clip_carry)
        base["roll_yield_3m"] = base["roll_yield_3m"].clip(-clip_carry, clip_carry)
        base["carry_score"] = base["carry_score"].clip(-clip_carry, clip_carry)
    if "liquidity_score" in base.columns:
        base["liquidity_score"] = pd.to_numeric(base["liquidity_score"], errors="coerce").fillna(1.0)
    else:
        base["liquidity_score"] = 1.0
    base["source_label"] = str(source_label)

    out = base[OUTPUT_COLUMNS].copy()
    for col in ("date", "release_date", "tradable_from"):
        out[col] = pd.to_datetime(out[col]).dt.date.astype(str)
    out = out.sort_values(["symbol", "tradable_from", "date"]).reset_index(drop=True)
    return out


def write_futures_curve_signal_csv(
    input_csv: str | Path,
    output_csv: str | Path,
    *,
    publication_lag_business_days: int = 1,
    source_label: str = "LOCAL_CSV",
    clip_carry: float = 1.0,
) -> Path:
    out = build_futures_curve_signal_frame(
        input_csv,
        publication_lag_business_days=publication_lag_business_days,
        source_label=source_label,
        clip_carry=clip_carry,
    )
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return output_path
