# Candidate EV Gate Results

## Purpose

Evaluate a simple, interpretable candidate-level expected-value gate before wiring anything into the event-driven strategy. The gate is tested walk-forward: every test year is scored only from historical candidate labels that would have matured before that year starts.

This avoids lookahead bias and answers whether a simple bucketed EV model is good enough to preserve or improve the current high-PF Engine B core.

## Implementation

- Script: `examples/evaluate_candidate_ev_gate.py`
- Input: candidate research CSV from `BarbellMacroWizardStrategy.candidate_research_frame()`
- Label: `side_fwd_return_21d`
- Tail estimate: 10th percentile of `side_mae_21d`
- Features / buckets:
  - symbol
  - side
  - theme
  - asset class
  - absolute score bucket
  - coherence bucket
  - trend bucket
  - realized-vol bucket
- Backoff levels:
  - symbol + side + theme + score bucket + coherence bucket
  - symbol + side + score bucket
  - asset class + side + score bucket
  - symbol + side
  - asset class + side
  - global historical mean

## Commands

Baseline candidate set:

```bash
.venv/bin/python examples/evaluate_candidate_ev_gate.py \
  --csv reports/market_wizards/global_macro_barbell_research_reversal_candidate_research.csv \
  --horizon 21 --top-n 3 --hurdle 0.0025 --tail-limit -0.10 \
  --min-obs 40 --start-year 2010 \
  --predictions-output reports/market_wizards/global_macro_barbell_research_reversal_ev_gate_predictions.csv \
  --report-output reports/market_wizards/global_macro_barbell_research_reversal_ev_gate_report.md
```

Expanded candidate set:

```bash
.venv/bin/python examples/evaluate_candidate_ev_gate.py \
  --csv reports/market_wizards/global_macro_barbell_research_reversal_expanded_candidate_research.csv \
  --horizon 21 --top-n 3 --hurdle 0.0025 --tail-limit -0.10 \
  --min-obs 40 --start-year 2010 \
  --predictions-output reports/market_wizards/global_macro_barbell_research_reversal_expanded_ev_gate_predictions.csv \
  --report-output reports/market_wizards/global_macro_barbell_research_reversal_expanded_ev_gate_report.md
```

## Results

### Baseline Candidate Set

| Variant | Count | Mean 21d Side Return | Hit Rate | PF |
| --- | ---: | ---: | ---: | ---: |
| Actual selected | 4,092 | 1.7963% | 60.56% | 1.9486 |
| Actual selected after EV gate | 2,370 | 1.2856% | 63.84% | 1.8793 |
| EV-ranked top 3 | 4,170 | 1.1326% | 62.61% | 1.9309 |

### Expanded Candidate Set

| Variant | Count | Mean 21d Side Return | Hit Rate | PF |
| --- | ---: | ---: | ---: | ---: |
| Actual selected | 4,344 | 1.4065% | 58.06% | 1.9004 |
| Actual selected after EV gate | 2,275 | 1.0982% | 61.36% | 1.7927 |
| EV-ranked top 3 | 5,975 | 0.9588% | 60.65% | 1.8482 |

## Interpretation

The simple bucketed EV gate is not good enough to promote into the live strategy.

It increases hit rate, but it lowers mean forward return and does not improve profit factor. That means the historical bucket model is filtering some winners alongside losers and is not capturing the episodic payoff structure that makes the current Engine B core valuable.

The expanded-universe output still supports the prior conclusion: added instruments are more useful as conditional risk reducers than direct alpha replacements. `TLT` and `IEF` show mild positive conditional EV in the EV-ranked selection, while `GLD`, `TIP`, and `USO` are not selected well by this simple model.

## Decision

Do not wire this EV gate into the event-driven strategy yet.

Keep it as research tooling and move next to a conditional hedge overlay test where TLT/IEF/GLD/TIP are evaluated as satellites around the unchanged core, not as replacements in the same top-N competition.
