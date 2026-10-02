# Track 1 / Track 2 Status

## Track 1: Current-Data Strategy Experiments

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/macro_barbell_experiment_analysis.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/macro_barbell_experiment_analysis.md`

Comparison table:

| Variant | CAGR | Sharpe | MaxDD | PF | Phase3 Ratio | Overlay Weight |
|---|---:|---:|---:|---:|---:|---:|
| step_d_concentration | 12.1141% | 0.8807 | -23.9935% | 4.3928 | 3.0797% | 0.2023% |
| phase3_profit_rate | 12.1636% | 0.8839 | -23.9949% | 4.4139 | 1.3113% | 0.2023% |
| overlay_routing | 12.2091% | 0.8881 | -23.9980% | 4.5340 | 1.3113% | 0.0000% |
| focused_reallocation | 12.1636% | 0.8839 | -23.9949% | 4.4139 | 1.3113% | 0.2023% |

Interpretation:
- Step D baseline is structurally broad enough to keep concentration research going.
- Phase3 profit-rate improves CAGR, Sharpe, and PF, but not through higher Phase 3 time-in-market. It selects fewer, better Phase 3 trades.
- Overlay routing is not validated as a live routing alpha source. The improvement occurs with zero live overlay activity, which means the gain comes from suppressing the additive overlay.
- Focused reallocation remains inert on this sample.

### Step D baseline episode decomposition

Engine B PnL by requested bucket:
- `2005-2007`: `0.00`
- `2007-2009`: `213,413.98`
- `2010-2013`: `90,598.05`
- `2014-2016`: `289,975.25`
- `2017-2019`: `91,343.08`
- `2020`: `529,884.07`
- `2021-2022`: `814,219.36`
- `2023-2024`: `70,497.33`

Phase 3 concentration is not a single-episode artifact.
Most important Phase 3 buckets:
- `2021-2022`: `20` Phase 3 trades, `75.66%` of bucket PnL from Phase 3 trades
- `2007-2009`: `11` Phase 3 trades, `39.92%` of bucket PnL from Phase 3 trades
- `2014-2016`: `4` Phase 3 trades, `25.50%` of bucket PnL from Phase 3 trades

Dominant instruments by episode:
- `2007-2009`: `USO`, `DBC`, `SPY`
- `2014-2016`: `USO`, `DBC`, `EEM`
- `2021-2022`: `DBC`, `EEM`, `USO`
- `2023-2024`: `SPY`, `EEM`

Conclusion:
- concentration is being carried by multiple episodes and multiple instruments
- the architecture is structurally sound enough to keep pressing the high-conviction path

## Track 2: Macro Data Population

Completed:
- richer PiT macro snapshot schema
- public series ingestion layer
- ALFRED-aware provider support
- US ALFRED template expanded to:
  - growth: `PAYEMS`, `INDPRO`, `RSAFS`
  - inflation: `CPIAUCSL`, `PPIFIS`
  - policy: `FEDFUNDS`, `T10Y2Y`
  - liquidity: `NFCI`
  - credit: `BAMLH0A0HYM2`, `BAMLC0A4CBBB`
- HTTPS/CA bundle fix for Python ingestion so St. Louis endpoints no longer fail on certificate verification

Current blocker:
- `FRED_API_KEY` is not set in the current environment
- result: the ALFRED/FRED ingestion path is operational, but a real US public build has not yet been executed here

Important validation result:
- after the CA-bundle fix, Python access to St. Louis endpoints now fails for the correct reason (`HTTP 400` missing API key) instead of SSL certificate failure

Next command once the key is available:

```bash
cd /Users/vincentpechstein/Downloads/Pixel-lab/Backtesting
source .venv/bin/activate

python examples/build_public_macro_from_config.py \
  --config-csv examples/data_templates/macro_public_series_template.csv \
  --events-output data/market_wizards/macro_factor_events_public.csv \
  --snapshots-output data/market_wizards/macro_regimes_public.csv \
  --fred-api-key "$FRED_API_KEY"
```

Immediate next evaluation after that build:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --macro-csv data/market_wizards/macro_regimes_public.csv \
  --barbell-extreme-concentration-layer \
  --start 2005-01-01 --end 2025-01-01 --interval 1d --no-report
```
