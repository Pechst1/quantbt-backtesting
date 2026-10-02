from __future__ import annotations

import argparse
from pathlib import Path

from quantbt.altdata import PublicMacroBuilderConfig, write_public_macro_snapshot_csv


DEFAULT_EVENTS = Path(__file__).resolve().parent / "data_templates" / "macro_factor_events_template.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "market_wizards" / "macro_regimes_public.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an event-driven public macro dataset from factor release events.",
    )
    parser.add_argument("--events-csv", type=str, default=str(DEFAULT_EVENTS))
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--z-window", type=int, default=36)
    parser.add_argument("--min-observations", type=int, default=12)
    parser.add_argument("--publication-lag-business-days", type=int, default=5)
    parser.add_argument("--freshness-half-life-days", type=int, default=45)
    parser.add_argument("--clip-zscore", type=float, default=3.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = PublicMacroBuilderConfig(
        z_window=max(int(args.z_window), 2),
        min_observations=max(int(args.min_observations), 2),
        publication_lag_business_days=max(int(args.publication_lag_business_days), 0),
        freshness_half_life_days=max(int(args.freshness_half_life_days), 1),
        clip_zscore=max(float(args.clip_zscore), 0.5),
    )
    output = write_public_macro_snapshot_csv(args.events_csv, args.output, config=config)
    print(f"Wrote public macro dataset: {output}")


if __name__ == "__main__":
    main()
