# Revised Risk Experiment Results

Date: 2026-05-06

This pass implemented the conservative follow-up to the failed full optimization bundle:

- Expanded universe as candidate replacement only: original thresholds, original `top_n=3`, no adaptive-threshold relaxation.
- Independent safety layer: drawdown throttle plus position-level NAV stop.
- Combined revised-risk preset: current research baseline + liquidation reversal + expanded candidate universe + safety layer.

## New CLI Flags

```bash
--barbell-expanded-universe-layer
--barbell-safety-layer
--barbell-revised-risk-layer
```

`--barbell-revised-risk-layer` expands to:

```bash
--barbell-research-baseline
--barbell-liquidation-reversal-layer
--barbell-expanded-universe-layer
--barbell-safety-layer
```

It intentionally does **not** enable the previously rejected full optimization components: adaptive thresholds, top-N increase, vol targeting, dynamic Engine A, Phase 4, cross-asset booster, or melt-up reversal.

## Test Status

```text
57 passed
```

## Backtest Window

`2005-01-01` to `2025-01-01`, daily bars, same execution model as prior runs.

`EMB` remained optional and unavailable in this sandbox because Yahoo DNS resolution failed. The expanded-universe tests still used `TLT`, `GLD`, `TIP`, and `IEF` from cache.

## Results

| Variant | CAGR | Sharpe | Sortino | Max DD | Calmar | Win Rate | Profit Factor | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Current best: baseline + liquidation reversal | 12.2917% | 0.9007 | 1.0621 | -23.9965% | 0.5122 | 60.63% | 4.4314 | 574 |
| Expanded candidate universe only | 9.1843% | 0.7675 | 0.8851 | -15.8774% | 0.5785 | 54.41% | 2.6448 | 623 |
| Safety layer only | 11.7557% | 0.8832 | 1.0282 | -24.1813% | 0.4862 | 57.86% | 4.2837 | 693 |
| Revised-risk combined | 8.7090% | 0.7475 | 0.8608 | -16.7770% | 0.5191 | 55.27% | 2.5729 | 702 |

## Diagnostics

| Diagnostic | Current Best | Expanded Only | Safety Only | Revised Risk |
|---|---:|---:|---:|---:|
| Engine A avg gross | 20.05% | 19.85% | 19.17% | 19.57% |
| Engine B avg gross | 34.44% | 37.12% | 33.28% | 36.14% |
| Engine B dormant ratio | 46.67% | 44.25% | 46.67% | 44.25% |
| Phase 3 active ratio | 1.31% | 2.98% | 1.39% | 2.98% |
| Engine B PF | 4.74 | 2.67 | 4.57 | 2.58 |
| Engine B net PnL | 2.18m | 1.13m | 1.97m | 1.02m |
| Drawdown throttle scalar mean | 1.00 | 1.00 | 0.945 | 0.969 |
| NAV-stop active ratio | 0.00% | 0.00% | 1.55% | 2.05% |

## Interpretation

The revised experiment confirms the earlier lesson: broadening the candidate set reduces drawdown but dilutes the return engine.

Expanded candidate universe did exactly what expected from the prior ablation:

- Max drawdown improved materially: `-24.00%` to `-15.88%`.
- Calmar improved: `0.512` to `0.578`.
- But CAGR fell from `12.29%` to `9.18%`.
- Engine B PF collapsed from `4.74` to `2.67`.

Safety layer did not help on current data:

- CAGR fell to `11.76%`.
- Max drawdown did not improve; it slightly worsened.
- NAV stops increased closed trades and reduced average trade quality.
- Drawdown throttle reduced exposure after losses but did not prevent the largest drawdown path.

Combined revised-risk layer is not promotable:

- CAGR fell to `8.71%`.
- Sharpe fell to `0.75`.
- Max drawdown improved to `-16.78%`, but the return sacrifice was too large.
- Calmar improved only marginally versus current best (`0.519` vs `0.512`), not enough to justify the lower CAGR and lower Sharpe.

## Decision

Do **not** promote any of these revised-risk variants.

Keep the current best baseline:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Use the new flags only for future research:

```bash
# Expanded candidate universe only
--barbell-research-baseline --barbell-liquidation-reversal-layer --barbell-expanded-universe-layer

# Safety controls only
--barbell-research-baseline --barbell-liquidation-reversal-layer --barbell-safety-layer

# Combined revised-risk experiment
--barbell-revised-risk-layer
```

## Next Research Recommendation

The current data now rejects both broad optimization and conservative expanded-universe/safety variants. The next credible research step is not another heuristic layer. It is infrastructure:

1. Point-in-time macro surprise data.
2. Futures-based commodity/duration instruments.
3. Factor-risk attribution/caps using proper factor exposures, not ticker-level caps.
4. Per-episode attribution to identify where expanded universe improves drawdown and whether that can be used as a hedge rather than a replacement alpha sleeve.
