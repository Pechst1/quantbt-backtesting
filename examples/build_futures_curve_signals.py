from __future__ import annotations

import argparse
from pathlib import Path

from quantbt.altdata import write_futures_curve_signal_csv


DEFAULT_INPUT = Path(__file__).resolve().parent / "data_templates" / "futures_curve_contract_prices_template.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "market_wizards" / "futures_curve_signals.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build point-in-time futures curve/carry signals from public or local contract price CSVs.",
    )
    parser.add_argument("--input-csv", type=str, default=str(DEFAULT_INPUT))
    parser.add_argument("--output-csv", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--publication-lag-business-days", type=int, default=1)
    parser.add_argument("--source-label", type=str, default="LOCAL_CSV")
    parser.add_argument("--clip-carry", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = write_futures_curve_signal_csv(
        args.input_csv,
        args.output_csv,
        publication_lag_business_days=max(int(args.publication_lag_business_days), 0),
        source_label=args.source_label,
        clip_carry=max(float(args.clip_carry), 0.0),
    )
    print(f"Wrote futures curve signal dataset: {output}")


if __name__ == "__main__":
    main()
