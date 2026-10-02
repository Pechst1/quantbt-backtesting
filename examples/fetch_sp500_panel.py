"""
Download the survivorship-aware S&P 500 price panel and derive point-in-time membership.

Source: https://github.com/Johnbrick123/sp500-data (release "data").
- prices.parquet: daily OHLCV + adj_close for current and former S&P 500 members
  (Yahoo, Tiingo and Quandl WIKI history; delisted names keep a -YYYYMM suffix).
- members.parquet: index members on every membership-change date since 1996.

The 160 MB price file goes to .cache/ (gitignored). The membership is converted into
QuantBT's interval CSV format at data/index_membership/sp500_membership_pit_panel.csv,
using the same ticker spelling as the price file.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd

RELEASE_URL = "https://github.com/Johnbrick123/sp500-data/releases/download/data/{name}"
FILES = ("prices.parquet", "members.parquet")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", default=".cache/sp500_panel")
    parser.add_argument("--membership-output", default="data/index_membership/sp500_membership_pit_panel.csv")
    parser.add_argument("--force", action="store_true", help="Re-download even if files exist.")
    return parser.parse_args()


def download(cache_dir: Path, force: bool) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = cache_dir / name
        if target.exists() and not force:
            continue
        print(f"Downloading {name} ...")
        urllib.request.urlretrieve(RELEASE_URL.format(name=name), target)


def membership_intervals(members: pd.DataFrame) -> pd.DataFrame:
    """Snapshots on change dates -> one row per contiguous membership spell."""
    snapshot_dates = sorted(members["date"].unique())
    next_date = dict(zip(snapshot_dates[:-1], snapshot_dates[1:]))
    rows: list[dict[str, object]] = []
    for ticker, group in members.groupby("ticker"):
        present = set(group["date"])
        start = None
        prev = None
        for snap in snapshot_dates:
            if snap in present:
                if start is None:
                    start = snap
                prev = snap
            elif start is not None:
                rows.append({"symbol": ticker, "effective_from": start, "effective_to": snap - pd.Timedelta(days=1)})
                start = None
        if start is not None:
            last_is_final = prev == snapshot_dates[-1]
            end = None if last_is_final else next_date[prev] - pd.Timedelta(days=1)
            rows.append({"symbol": ticker, "effective_from": start, "effective_to": end})
    out = pd.DataFrame(rows)
    out.insert(0, "index_name", "SP500")
    out["effective_from"] = pd.to_datetime(out["effective_from"]).dt.date
    out["effective_to"] = pd.to_datetime(out["effective_to"]).dt.date
    return out.sort_values(["symbol", "effective_from"]).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    download(cache_dir, args.force)
    members = pd.read_parquet(cache_dir / "members.parquet")
    members["date"] = pd.to_datetime(members["date"])
    intervals = membership_intervals(members)
    output = Path(args.membership_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    intervals.to_csv(output, index=False)
    print(f"Wrote {len(intervals)} membership spells for {intervals['symbol'].nunique()} symbols to {output}")


if __name__ == "__main__":
    main()
