# Optimization Bundle Implementation And Backtest Results

Date: 2026-05-06

Input brief: `/Users/vincentpechstein/Downloads/concentrated_macro_dislocation_barbell_optimization.md`

## Implemented Code Changes

The proposed optimization bundle was implemented behind an opt-in flag:

```bash
--barbell-optimization-layer
```

Implemented feasible layers:

- Expanded Engine B universe: `SPY`, `EEM`, `DBC`, `USO`, `UUP`, `TLT`, `GLD`, `TIP`, `EMB`, `IEF`.
- `EMB` promoted to `Credit` in the security master, but it remains optional because no local market-data cache exists and Yahoo DNS is unavailable in the sandbox.
- Dynamic Engine A carry sizing by US credit z-score.
- Adaptive Engine B thresholds by coherence and velocity signal.
- Correlation-aware top-N selection.
- Asymmetric trend filter with faster short lookback and graded ATR-distance penalty.
- Phase 4 super-press tier.
- Cross-asset confirmation booster.
- Bilateral melt-up reversal sleeve.
- Portfolio volatility targeting.
- Drawdown throttle.
- Position-level NAV stop.
- Diagnostics for vol scalar, drawdown scalar, cross-asset boost, melt-up sleeve, and NAV stops.

Not implemented because they require infrastructure/venue beyond the current engine:

- True options tail hedge.
- True options convexity replacement.
- First-release macro data validation.
- Futures venue/accounting beyond the existing Yahoo futures proxy path.

## Commands

Current best reference:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Optimization bundle:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-optimization-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

## Main Backtest Results

| Variant | CAGR | Sharpe | Sortino | Max DD | Calmar | Win Rate | Profit Factor | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Research baseline | 12.2068% | 0.8879 | 1.0519 | -23.9980% | 0.5087 | 61.33% | 4.5426 | 525 |
| Current best: baseline + liquidation reversal | 12.2917% | 0.9007 | 1.0621 | -23.9965% | 0.5122 | 60.63% | 4.4314 | 574 |
| Full optimization bundle | 6.5084% | 0.5964 | 0.7152 | -20.3869% | 0.3192 | 58.24% | 1.7736 | 1,633 |

## Diagnostic Comparison

| Diagnostic | Current Best | Optimization Bundle |
|---|---:|---:|
| Engine A avg gross | 20.05% | 19.10% |
| Engine B avg gross | 34.44% | 47.14% |
| Engine B dormant ratio | 46.67% | 42.84% |
| Phase 3+ active ratio | 1.31% | 2.64% |
| Engine A reallocation active ratio | 2.28% | 3.36% |
| Liquidation reversal active ratio | 0.44% | 0.36% |
| Vol target scalar mean | 1.00 | 1.23 |
| Drawdown throttle scalar mean | 1.00 | 0.87 |
| Portfolio scalar mean | 1.00 | 1.05 |
| Engine B PF | 4.74 | 1.72 |
| Engine A PF | 2.09 | 2.75 |

## Ablations

| Variant | CAGR | Sharpe | Max DD | Profit Factor | Trades |
|---|---:|---:|---:|---:|---:|
| Expanded universe only | 9.1843% | 0.7675 | -15.8774% | 2.6448 | 623 |
| Optimization without vol/DD/NAV risk scalars | 9.7127% | 0.6580 | -25.6071% | 2.0090 | 1,080 |
| Optimization on old universe | 9.6917% | 0.6999 | -26.0652% | 2.7001 | 1,420 |
| Current best + vol targeting only | 10.3667% | 0.8733 | -21.8686% | 3.6202 | 921 |
| Current best + dynamic Engine A only | 12.0414% | 0.8784 | -24.0605% | 4.4607 | 618 |

## Conclusion

The proposed optimization bundle is implemented but rejected as a promoted baseline on the current proxy data.

The central failure is not leverage or drawdown control. The bundle activates too many lower-quality Engine B trades. Trade count rose from 574 to 1,633 while profit factor collapsed from 4.43 to 1.77. This confirms that the current strategy's edge remains concentrated in selective dislocation events. Broadening the universe and relaxing thresholds diluted the signal faster than vol targeting could monetize unused risk budget.

The current best baseline remains:

```bash
--barbell-research-baseline --barbell-liquidation-reversal-layer
```

The new optimization layers should remain research-only behind `--barbell-optimization-layer` until better macro data and cleaner instruments are available.

## Next Recommended Research

1. Do not promote the full bundle.
2. Keep the implemented flag for future A/B testing.
3. Re-run only after first-release ALFRED/FRED macro data is populated.
4. Re-test universe expansion with proper EM macro and `EMB` market data available.
5. Re-test commodity futures instead of `USO`/`DBC` before judging the expanded commodity sleeve.
6. If optimizing on current data, focus on per-episode attribution and stricter gating, not broader deployment.
