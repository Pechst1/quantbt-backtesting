from __future__ import annotations

import argparse
from pathlib import Path

from quantbt.altdata import write_eia_wti_futures_curve_csv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "market_wizards" / "futures_curve_signals_eia_wti.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a point-in-time WTI futures curve from official EIA NYMEX contracts 1-4.",
    )
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cache-dir", default=str(ROOT / ".cache" / "alt_data" / "eia_wti"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--publication-lag-business-days", type=int, default=1)
    parser.add_argument("--clip-carry", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = write_eia_wti_futures_curve_csv(
        args.output_csv,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
        publication_lag_business_days=max(int(args.publication_lag_business_days), 0),
        clip_carry=max(float(args.clip_carry), 0.0),
    )
    print(f"Wrote official EIA WTI futures curve: {output}")


if __name__ == "__main__":
    main()
