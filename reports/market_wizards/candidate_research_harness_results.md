# Candidate Research Harness Results

## What Changed

Implemented a passive candidate-level research export for `BarbellMacroWizardStrategy`. The export records every Engine B candidate on every trading day, not only executed trades, then labels each row after the simulation with forward returns, side-adjusted returns, volatility-normalized returns, MAE, and MFE over 5/10/21/63 trading-day horizons.

This keeps the live strategy event-driven and point-in-time: no forward labels are used during simulation. Labels are attached only after the run through `candidate_research_frame()`.

## Files

- Strategy export: `src/quantbt/strategies/market_wizards.py`
- Runner artifact wiring: `examples/run_market_wizards_strategies.py`
- Reproducible analyzer: `examples/analyze_candidate_research.py`
- Baseline candidate CSV: `reports/market_wizards/global_macro_barbell_research_reversal_candidate_research.csv`
- Baseline candidate summary: `reports/market_wizards/global_macro_barbell_research_reversal_candidate_summary.md`
- Expanded-universe candidate CSV: `reports/market_wizards/global_macro_barbell_research_reversal_expanded_candidate_research.csv`
- Expanded-universe candidate summary: `reports/market_wizards/global_macro_barbell_research_reversal_expanded_candidate_summary.md`

## Baseline Run

Command:

```bash
MPLCONFIGDIR=/private/tmp/mpl .venv/bin/python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Result:

- CAGR: 12.2917%
- Sharpe: 0.9007
- MaxDD: -23.9965%
- Profit factor: 4.4314
- Closed trades: 574
- Candidate rows: 24,029
- 21d forward-label coverage: 99.56%

Selected 21d side-adjusted candidate returns by symbol:

- USO: mean 4.8948%, count 806
- EEM: mean 1.5192%, count 1,706
- DBC: mean 1.4583%, count 1,144
- SPY: mean 0.9665%, count 1,751

## Expanded-Universe Diagnostic

Command:

```bash
MPLCONFIGDIR=/private/tmp/mpl .venv/bin/python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-expanded-universe-layer \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Result:

- CAGR: 9.1843%
- Sharpe: 0.7675
- MaxDD: -15.8774%
- Profit factor: 2.6448
- Closed trades: 623
- Candidate rows: 44,161
- 21d forward-label coverage: 99.57%

Selected 21d side-adjusted candidate returns by added instruments:

- TLT: mean 0.6329%, count 498
- IEF: mean -0.0162%, count 316
- TIP: mean -0.9881%, count 24
- GLD: mean -1.5829%, count 587

## Interpretation

The expanded universe reduced drawdown, but the candidate-level labels confirm that some added instruments were poor alpha candidates under the current hand-written macro templates. TLT had weak positive conditional EV, while GLD, TIP, and IEF were not strong enough to compete directly with the original dislocation instruments.

This supports the revised research direction: preserve the concentrated Engine B core, then use expanded instruments as conditional hedges or separate sleeves, not as direct replacements in the same top-N selector.

## Validation

- `57 passed` via `.venv/bin/python -m pytest -q`
- The optional `EMB` sensor could not be downloaded in the sandbox because Yahoo DNS is unavailable; the runner continued without it.
