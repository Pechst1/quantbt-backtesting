from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SP500_SOURCE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "sp500_ticker_start_end.csv"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download and normalize historical index membership data into "
            "QuantBT's point-in-time format."
        )
    )
    parser.add_argument(
        "--index",
        type=str,
        default="sp500",
        choices=["sp500"],
        help="Index to download.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/index_membership/sp500_membership_full.csv",
        help="Output CSV path in QuantBT PiT format.",
    )
    parser.add_argument(
        "--source-url",
        type=str,
        default=SP500_SOURCE_URL,
        help="Override source URL.",
    )
    return parser.parse_args()


def _build_sp500_membership_frame(source_url: str) -> pd.DataFrame:
    raw = pd.read_csv(source_url)
    raw.columns = [str(col).strip().lower() for col in raw.columns]
    required = {"ticker", "start_date", "end_date"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing columns from source data: {sorted(missing)}")

    effective_from = pd.to_datetime(raw["start_date"], errors="coerce")
    effective_to = pd.to_datetime(raw["end_date"], errors="coerce")

    frame = pd.DataFrame(
        {
            "index_name": "SP500",
            "symbol": raw["ticker"].astype(str).str.upper().str.strip(),
            "effective_from": effective_from,
            "effective_to": effective_to,
        }
    )
    frame = frame.dropna(subset=["symbol", "effective_from"])
    frame = frame.sort_values(["symbol", "effective_from"]).reset_index(drop=True)
    return frame


def main() -> None:
    args = parse_args()
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.index != "sp500":
        raise ValueError(f"Unsupported index: {args.index}")

    frame = _build_sp500_membership_frame(args.source_url)
    output_frame = frame.copy()
    output_frame["effective_from"] = output_frame["effective_from"].dt.date.astype(str)
    output_frame["effective_to"] = output_frame["effective_to"].dt.date.astype("string").fillna("")
    output_frame.to_csv(output_path, index=False)

    min_date = frame["effective_from"].min().date().isoformat()
    closed = frame["effective_to"].dropna()
    max_date = closed.max().date().isoformat() if not closed.empty else "open-ended"
    print(f"Wrote {len(frame)} rows to {output_path}")
    print(f"Unique symbols: {frame['symbol'].nunique()}")
    print(f"Coverage: {min_date} -> {max_date}")


if __name__ == "__main__":
    main()
