from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.altdata.futures_curve_builder import OUTPUT_COLUMNS
from quantbt.altdata.http import open_url


EIA_WTI_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/RCLC{rank}d.xls"
EIA_WTI_SOURCE = "EIA:NYMEX_WTI_RCLC1-4"


def _read_eia_contract_workbook(path_or_buffer: str | Path | BytesIO, rank: int) -> pd.DataFrame:
    frame = pd.read_excel(path_or_buffer, sheet_name="Data 1", skiprows=2)
    if frame.shape[1] < 2:
        raise ValueError(f"EIA WTI contract {rank} workbook has no price column")
    out = frame.iloc[:, :2].copy()
    out.columns = ["date", f"contract_{rank}_price"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce", utc=True).dt.tz_localize(None)
    out[f"contract_{rank}_price"] = pd.to_numeric(out[f"contract_{rank}_price"], errors="coerce")
    return out.dropna().drop_duplicates("date", keep="last").sort_values("date")


def fetch_eia_wti_contract_frames(
    *,
    cache_dir: str | Path = ".cache/alt_data/eia_wti",
    refresh: bool = False,
) -> dict[int, pd.DataFrame]:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    frames: dict[int, pd.DataFrame] = {}
    for rank in (1, 2, 3, 4):
        cache_path = cache / f"RCLC{rank}d.xls"
        if refresh or not cache_path.exists():
            with open_url(EIA_WTI_XLS_URL.format(rank=rank), timeout=60) as response:
                cache_path.write_bytes(response.read())
        frames[rank] = _read_eia_contract_workbook(cache_path, rank)
    return frames


def build_eia_wti_futures_curve_frame(
    contract_frames: dict[int, pd.DataFrame],
    *,
    publication_lag_business_days: int = 1,
    clip_carry: float = 1.0,
) -> pd.DataFrame:
    missing = {1, 2, 3, 4}.difference(contract_frames)
    if missing:
        raise ValueError(f"Missing EIA WTI contract ranks: {sorted(missing)}")

    merged = contract_frames[1].copy()
    for rank in (2, 3, 4):
        merged = merged.merge(contract_frames[rank], on="date", how="inner", validate="one_to_one")
    merged = merged.dropna().sort_values("date").reset_index(drop=True)
    if merged.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    front = merged["contract_1_price"].astype(float)
    second = merged["contract_2_price"].astype(float)
    third = merged["contract_3_price"].astype(float)
    fourth = merged["contract_4_price"].astype(float)
    valid = (second.abs() > 1e-9) & (third.abs() > 1e-9) & (fourth.abs() > 1e-9)
    merged = merged.loc[valid].copy()
    front, second, third, fourth = (series.loc[valid] for series in (front, second, third, fourth))

    out = pd.DataFrame(index=merged.index)
    out["symbol"] = "CL=F"
    out["date"] = merged["date"]
    out["release_date"] = merged["date"]
    out["tradable_from"] = merged["date"] + pd.offsets.BDay(max(int(publication_lag_business_days), 0))
    out["front_contract"] = "RCLC1"
    out["second_contract"] = "RCLC2"
    out["third_contract"] = "RCLC3"
    out["fourth_contract"] = "RCLC4"
    out["front_price"] = front
    out["second_price"] = second
    out["third_price"] = third
    out["fourth_price"] = fourth
    out["roll_yield_1m"] = ((front - second) / second.abs()) * 12.0
    out["roll_yield_3m"] = ((front - fourth) / fourth.abs()) * 4.0
    out["backwardation_score"] = (front - second) / second.abs()
    out["carry_score"] = 0.70 * out["roll_yield_1m"] + 0.30 * out["roll_yield_3m"]
    if clip_carry > 0.0:
        for column in ("roll_yield_1m", "roll_yield_3m", "carry_score"):
            out[column] = out[column].clip(-clip_carry, clip_carry)
    out["liquidity_score"] = 1.0
    out["source_label"] = EIA_WTI_SOURCE
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=["carry_score"])
    for column in ("date", "release_date", "tradable_from"):
        out[column] = pd.to_datetime(out[column]).dt.date.astype(str)
    return out[OUTPUT_COLUMNS].sort_values(["tradable_from", "date"]).reset_index(drop=True)


def write_eia_wti_futures_curve_csv(
    output_csv: str | Path,
    *,
    cache_dir: str | Path = ".cache/alt_data/eia_wti",
    refresh: bool = False,
    publication_lag_business_days: int = 1,
    clip_carry: float = 1.0,
) -> Path:
    frames = fetch_eia_wti_contract_frames(cache_dir=cache_dir, refresh=refresh)
    output = build_eia_wti_futures_curve_frame(
        frames,
        publication_lag_business_days=publication_lag_business_days,
        clip_carry=clip_carry,
    )
    path = Path(output_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False)
    return path
