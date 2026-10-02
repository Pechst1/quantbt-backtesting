# Time-Series Momentum Sleeve Results

## Purpose

Test whether a separate trend-following sleeve can earn during Engine B dormant periods without diluting the concentrated macro-dislocation core.

## Implementation

Implemented `--barbell-trend-sleeve-layer`.

Initial version:

- Monthly/weekly independent sleeve across SPY, EEM, TLT, IEF, GLD, DBC, USO, UUP.
- Signal: average sign of 1/3/6/12-month returns.
- Vol-aware ranking and capped weights.
- Kept separate from Engine B top-N selection.

The first implementation resized too often and became effectively always-on, so it was tightened:

- Monthly signal refresh.
- Activate only when Engine B core gross is below 5%.
- Max sleeve gross reduced to 12%.
- Max symbol weight reduced to 4%.
- Require all lookback horizons to agree (`abs(score) == 1.0`).

## Results

| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current best baseline | 12.2917% | 0.9007 | -23.9965% | 0.5122 | 4.4314 | 574 |
| Trend sleeve, first implementation | 10.0085% | 0.7708 | -23.0239% | 0.4347 | 2.4442 | 5,488 |
| Trend sleeve, dormant-only | 12.1043% | 0.8908 | -23.9968% | 0.5044 | 4.0087 | 862 |
| Hedge + dormant-only trend | 12.1143% | 0.8971 | -22.4840% | 0.5388 | 3.8921 | 994 |

## Diagnostics

Dormant-only trend sleeve:

- Trend active ratio: 36.30%
- Average trend gross: 3.40%
- Engine B dormant ratio: 46.67%
- Engine B PF: 4.44, down from 4.74 baseline Engine B PF

## Interpretation

The ETF-proxy time-series momentum sleeve is not accretive in the current architecture.

The first version failed because it became an always-on allocator and diluted the high-PF dislocation profile. The tightened dormant-only version is cleaner, but still fails to improve CAGR, drawdown, or PF versus the current best. Combined with the conditional hedge layer, it preserves some drawdown improvement but still reduces CAGR and PF.

This does not reject trend following as a research direction. It rejects this ETF-proxy implementation. A proper futures trend sleeve may still work because futures provide cleaner commodity/rates/FX expression, lower financing drag, and better risk sizing.

## Decision

Do not promote `--barbell-trend-sleeve-layer` into the current research baseline.

Keep the implementation available for future comparison, but the next research step should focus on either:

1. A proper futures-based trend sleeve with cleaner instruments and DV01/roll-aware sizing.
2. Carry-minus-crash-risk improvements to Engine A.
