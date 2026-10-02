from __future__ import annotations

import argparse
import os
from pathlib import Path

from quantbt.altdata import (
    PublicMacroBuilderConfig,
    write_macro_surprise_event_csv,
    write_public_macro_events_csv,
    write_public_macro_snapshot_csv,
)


DEFAULT_CONFIG = Path(__file__).resolve().parent / "data_templates" / "macro_public_series_template.csv"
DEFAULT_EVENTS = Path(__file__).resolve().parents[1] / "data" / "market_wizards" / "macro_factor_events_public.csv"
DEFAULT_SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "market_wizards" / "macro_regimes_public.csv"
DEFAULT_SURPRISES = Path(__file__).resolve().parents[1] / "data" / "market_wizards" / "macro_surprises_public.csv"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "alt_data" / "macro_series"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build public macro factor events and PiT macro snapshots from a provider config.",
    )
    parser.add_argument("--config-csv", type=str, default=str(DEFAULT_CONFIG))
    parser.add_argument("--events-output", type=str, default=str(DEFAULT_EVENTS))
    parser.add_argument("--snapshots-output", type=str, default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--surprises-output", type=str, default=str(DEFAULT_SURPRISES))
    parser.add_argument("--cache-dir", type=str, default=str(DEFAULT_CACHE))
    parser.add_argument("--refresh", action="store_true", help="Force refetch of remote series.")
    parser.add_argument("--fred-api-key", type=str, default="")
    parser.add_argument("--z-window", type=int, default=36)
    parser.add_argument("--min-observations", type=int, default=12)
    parser.add_argument("--publication-lag-business-days", type=int, default=5)
    parser.add_argument("--freshness-half-life-days", type=int, default=45)
    parser.add_argument("--clip-zscore", type=float, default=3.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fred_api_key = args.fred_api_key.strip() or os.environ.get("FRED_API_KEY", "").strip() or None
    events_path = write_public_macro_events_csv(
        args.config_csv,
        args.events_output,
        cache_dir=args.cache_dir,
        refresh=bool(args.refresh),
        fred_api_key=fred_api_key,
    )
    config = PublicMacroBuilderConfig(
        z_window=max(int(args.z_window), 2),
        min_observations=max(int(args.min_observations), 2),
        publication_lag_business_days=max(int(args.publication_lag_business_days), 0),
        freshness_half_life_days=max(int(args.freshness_half_life_days), 1),
        clip_zscore=max(float(args.clip_zscore), 0.5),
    )
    snapshots_path = write_public_macro_snapshot_csv(
        events_path,
        args.snapshots_output,
        config=config,
    )
    surprises_path = write_macro_surprise_event_csv(
        events_path,
        args.surprises_output,
        config=config,
    )
    print(f"Wrote public macro factor events: {events_path}")
    print(f"Wrote public macro snapshot dataset: {snapshots_path}")
    print(f"Wrote public macro surprise proxy dataset: {surprises_path}")


if __name__ == "__main__":
    main()
