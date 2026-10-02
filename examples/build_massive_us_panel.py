"""
Build an all-US common-stock daily panel from Massive (formerly Polygon) grouped daily bars.

Needs MASSIVE_API_KEY. Downloads one grouped-daily file per business day (split-adjusted,
not dividend-adjusted) plus the list of active and delisted common stocks (type CS), and
writes .cache/massive_us/prices.parquet in the ParquetPanelSource layout. SPY and QQQ are
kept as benchmark / market-filter series. Delisted names are included, so the panel has no
survivorship bias; ticker reuse by a different company is not separated.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://api.massive.com"


def get(url: str) -> dict:
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except Exception as exc:  # network hiccups
            print("retry", exc, flush=True)
            time.sleep(2**attempt)
    raise RuntimeError(f"giving up on {url.split('apiKey')[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-10-04")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--raw-dir", default=".cache/massive_grouped")
    parser.add_argument("--output", default=".cache/massive_us/prices.parquet")
    args = parser.parse_args()
    key = os.environ["MASSIVE_API_KEY"]
    raw = Path(args.raw_dir)
    raw.mkdir(parents=True, exist_ok=True)

    for day in pd.bdate_range(args.start, args.end):
        target = raw / f"{day.date()}.parquet"
        if target.exists():
            continue
        payload = get(f"{API}/v2/aggs/grouped/locale/us/market/stocks/{day.date()}?adjusted=true&apiKey={key}")
        if payload.get("results"):
            pd.DataFrame(payload["results"])[["T", "o", "h", "l", "c", "v"]].to_parquet(target)

    tickers_csv = raw / "tickers_cs.csv"
    if not tickers_csv.exists():
        rows = []
        for active in ("true", "false"):
            url = f"{API}/v3/reference/tickers?market=stocks&type=CS&active={active}&limit=1000&apiKey={key}"
            while url:
                payload = get(url)
                rows += [{"ticker": r["ticker"], "active": r.get("active")} for r in payload.get("results", [])]
                url = payload.get("next_url")
                url = f"{url}&apiKey={key}" if url else None
        pd.DataFrame(rows).to_csv(tickers_csv, index=False)
    keep = set(pd.read_csv(tickers_csv)["ticker"].astype(str)) | {"SPY", "QQQ"}

    frames = []
    for path in sorted(raw.glob("*.parquet")):
        frame = pd.read_parquet(path)
        frame = frame[frame["T"].isin(keep)]
        frame.insert(0, "date", pd.Timestamp(path.stem))
        frames.append(frame)
    panel = pd.concat(frames, ignore_index=True).rename(columns={"T": "ticker", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    panel["adj_close"] = panel["close"]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out, index=False)
    print(f"Wrote {len(panel):,} rows for {panel['ticker'].nunique():,} tickers to {out}")


if __name__ == "__main__":
    main()
