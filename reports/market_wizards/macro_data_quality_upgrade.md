# Macro Data Quality Upgrade

## Problem

The macro strategy stack was already point-in-time aware at the engine level, but the default macro input file
`/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/data/market_wizards/macro_regimes.csv`
was still a smooth monthly proxy series. That limited:

- extreme-regime activation density
- coherence/velocity usefulness
- within-month macro state updates
- credibility of any timing-layer research built on top of the macro feed

## Implemented

### 1. Event-driven macro snapshot schema

`/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/altdata/models.py`
now supports richer PiT macro snapshots with:

- `snapshot_id`
- `quality_score`
- `coverage_ratio`
- `source_label`
- `cache_key`

This allows multiple valid macro updates inside the same calendar month.

### 2. CSV macro source upgraded for event-driven inputs

`/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/altdata/csv_sources.py`
now supports two modes:

- legacy monthly files: keeps first release per `region/date`
- event-driven files with `snapshot_id`: preserves multiple PiT updates per period

It also reads and forwards quality metadata to the strategy layer.

### 3. Strategy cache invalidation fixed

`/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/strategies/market_wizards.py`
previously cached macro state by `(region, as_of)` only. That is insufficient when multiple updates share the same period date.

It now keys the cache off `MacroSnapshot.cache_key`, which includes snapshot identity and tradable timing.

### 4. Quality-aware macro scoring

The macro strategies now multiply raw macro score by `quality_score` when the source provides it.

This keeps legacy behavior unchanged because legacy snapshots default to `quality_score = 1.0`, while richer datasets can downweight thin or stale composite states naturally.

### 5. Public macro dataset builder

New module:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/altdata/macro_builder.py`

New CLI entrypoint:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/examples/build_public_macro_dataset.py`

The builder consumes public macro factor release events with schema:

- `region`
- `factor`
- `date`
- `release_date`
- `tradable_from` optional
- `value`
- `series` optional
- `source` optional
- `transform` optional
- `weight` optional

Supported transforms:

- `level`
- `diff_1`
- `diff_12`
- `pct_change_1`
- `pct_change_12`
- `zscore`

The builder produces:

- event-driven region snapshots
- automatic `GLOBAL` snapshots
- `snapshot_id`
- `quality_score`
- `coverage_ratio`
- `source_label`

### 6. Public series ingestion layer

New module:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/altdata/macro_public_ingest.py`

New CLI entrypoint:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/examples/build_public_macro_from_config.py`

This adds a sourcing path before the factor-event builder:

`public series config -> normalized factor events -> PiT macro snapshots`

Supported providers:
- `LOCAL_CSV`
- `CSV_URL`
- `FRED_API`
- `ALFRED_API`

Supported release-date behavior:
- explicit `release_date_col`
- explicit `tradable_from_col`
- `realtime_start` fallback for FRED-style vintage data
- derived release/tradable dates when only observation dates exist

This is still not a substitute for true first-release vintages, but it removes the need to hand-author factor-event CSVs.
For US series, `ALFRED_API` is now the correct path for first-release research. `FRED_API` remains available for latest-known public series when vintage discipline is not available.

### 7. Example template and runner support

Added template:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/examples/data_templates/macro_factor_events_template.csv`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/examples/data_templates/macro_public_series_template.csv`

Runner update:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/examples/run_market_wizards_strategies.py`

The runner now:

- prefers `macro_regimes_public.csv` when it exists
- otherwise falls back to the legacy `macro_regimes.csv`
- also accepts `--macro-csv` explicitly

## Validation

Tests:
- `51 passed`

New tests cover:

- legacy first-release behavior remains intact
- event-driven macro files preserve multiple snapshots with `snapshot_id`
- macro builder emits PiT region/global snapshots
- strategy macro cache invalidates on a new same-period snapshot

Smoke validation:

- built a sample public macro file from the new template to `/tmp/macro_regimes_public_smoke.csv`
- ran `global_macro_barbell` against that generated file successfully

## Important limitation

This upgrade fixes the architecture and ingestion path, not the economic content by itself.

The repo still does **not** ship a real full-history first-release macro event dataset. Until real public factor-release events are loaded into the builder, the production research baseline should continue to use:

- legacy file for baseline continuity
- new builder path for macro-data research

## Immediate next move

Populate the builder with a real public factor-event dataset. The highest-value path is:

1. US first-release macro events via ALFRED/FRED style exports
2. Europe/Japan/EM public macro event feeds or curated public CSV exports
3. rebuild `macro_regimes_public.csv`
4. rerun Step D and the shelved velocity layer against that dataset

The main research question after this upgrade is no longer whether the engine can consume richer macro updates.
It can. The next question is whether better macro vintages materially improve:

- extreme-regime activation
- Phase 3 promotion quality
- usefulness of macro velocity
- crisis timing of Engine B
