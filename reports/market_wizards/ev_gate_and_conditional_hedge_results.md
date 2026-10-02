# EV Gate And Conditional Hedge Results

## Scope

Continued from the candidate research harness with two next experiments:

1. Walk-forward candidate-level EV gate.
2. Production-style conditional hedge layer that keeps the original Engine B core unchanged and adds TLT/IEF/GLD/TIP as small satellites.

## Experiment 1: Candidate EV Gate

Implemented `examples/evaluate_candidate_ev_gate.py`.

The gate uses annual expanding walk-forward training and only uses labels that have matured before the test year starts. It is deliberately interpretable rather than black-box.

Result: not promoted.

| Variant | Count | Mean 21d Side Return | Hit Rate | PF |
| --- | ---: | ---: | ---: | ---: |
| Baseline actual selected | 4,092 | 1.7963% | 60.56% | 1.9486 |
| Baseline selected after EV gate | 2,370 | 1.2856% | 63.84% | 1.8793 |
| Baseline EV-ranked top 3 | 4,170 | 1.1326% | 62.61% | 1.9309 |
| Expanded actual selected | 4,344 | 1.4065% | 58.06% | 1.9004 |
| Expanded selected after EV gate | 2,275 | 1.0982% | 61.36% | 1.7927 |
| Expanded EV-ranked top 3 | 5,975 | 0.9588% | 60.65% | 1.8482 |

Interpretation: the simple bucketed EV gate raises hit rate but reduces mean return and does not improve PF. It is not good enough for live strategy gating. The tool remains useful for diagnostics and future model versions.

Artifacts:

- `reports/market_wizards/global_macro_barbell_research_reversal_ev_gate_report.md`
- `reports/market_wizards/global_macro_barbell_research_reversal_ev_gate_predictions.csv`
- `reports/market_wizards/global_macro_barbell_research_reversal_expanded_ev_gate_report.md`
- `reports/market_wizards/global_macro_barbell_research_reversal_expanded_ev_gate_predictions.csv`

## Experiment 2: Conditional Hedge Layer

Implemented `--barbell-conditional-hedge-layer`.

Design:

- Original Engine B dislocation symbols remain unchanged: SPY, EEM, DBC, USO, UUP.
- TLT/IEF/GLD/TIP are added to the data universe only as hedge symbols.
- They do not compete in Engine B top-N selection.
- Hedge activates only when core gross is high enough and the macro state supports the hedge.
- Duration hedges require growth/credit/liquidity stress to dominate inflation pressure.
- Gold/TIPS hedges require inflation or liquidity stress confirmation.
- Total hedge budget is capped at 15% gross and 6% per symbol.

Command:

```bash
MPLCONFIGDIR=/private/tmp/mpl .venv/bin/python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --barbell-conditional-hedge-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Result versus current best baseline:

| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current best baseline | 12.2917% | 0.9007 | -23.9965% | 0.5122 | 4.4314 | 574 |
| Baseline + conditional hedge | 12.2861% | 0.9053 | -22.4103% | 0.5482 | 4.2743 | 722 |

Diagnostics:

- Hedge active ratio: 22.47%
- Average hedge gross: 2.48%
- Engine B dormant ratio unchanged: 46.67%
- Engine B phase-3 ratio unchanged: 1.31%
- Engine B PF remains high: 4.76 on Engine B trades

Interpretation: the hedge layer is modestly useful as risk shaping, not return generation. It reduced max drawdown by roughly 1.6 percentage points with almost no CAGR loss, improving Calmar from 0.512 to 0.548 and Sharpe from 0.901 to 0.905.

Decision: keep as experimental. It is not a new alpha sleeve, but it is a better use of TLT/IEF/GLD/TIP than forcing them into Engine B top-N.

## Validation

- `.venv/bin/python -m pytest -q` passed with 57 tests.
- `EMB` remains optional and was skipped because Yahoo DNS/network access is unavailable in the sandbox.

## Next Research Step

The candidate EV gate did not work well enough. The conditional hedge layer did what it was supposed to do: reduce drawdown without materially diluting the concentrated core.

The next higher-EV implementation is a separate futures/time-series momentum sleeve, because it is structurally orthogonal to the dislocation engine and can operate while Engine B is dormant.
