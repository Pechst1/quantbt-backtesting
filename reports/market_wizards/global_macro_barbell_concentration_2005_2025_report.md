## Step D Extreme-Regime Concentration Layer

Window: `2005-01-01` to `2025-01-01`  
Baseline: `reports/market_wizards/global_macro_barbell_reallocation_2005_2025_final.json`  
Step D artifact: `reports/market_wizards/global_macro_barbell_concentration_2005_2025_final.json`

### Decision

Keep Step D. Promote it to the new research baseline.

It improves CAGR, Sharpe, Sortino, win rate, and profit factor versus Step A without worsening max drawdown in any meaningful way.

### What Was Added

- in extreme macro regimes, narrow Engine B from the default breadth to the top `2` expressions
- extreme regime requires:
  - current coherence above the historical `90th` percentile of prior active-regime coherence
  - at least one confirmed Engine B position already in Phase `>= 2`
- in extreme regimes, Phase 3 size increases from `1.3x` to `1.5x`
- Step D runs on top of Step A, not instead of it

### Results

| Metric | Step A | Step D |
|---|---:|---:|
| Annualized return | 11.6476% | 12.1141% |
| Sharpe | 0.8471 | 0.8807 |
| Sortino | 1.0015 | 1.0433 |
| Max drawdown | -23.9935% | -23.9935% |
| Profit factor | 4.0946 | 4.3928 |
| Win rate | 0.6146 | 0.6252 |
| Closed trades | 563 | 563 |

### Step D vs Step A

| Metric | Delta |
|---|---:|
| Annualized return | +0.4664% |
| Sharpe | +0.0336 |
| Sortino | +0.0418 |
| Max drawdown | +0.0000% |
| Profit factor | +0.2982 |
| Engine A net PnL | +3,723.24 |
| Engine B net PnL | +189,308.22 |

### Concentration Diagnostics

| Diagnostic | Value |
|---|---:|
| Extreme regime active ratio | 2.0664% |
| Extreme coherence mean | 0.2790 |
| Extreme threshold mean | 0.7909 |
| Engine B phase 3 ratio | 3.0797% |
| Engine B average gross | 0.3439 |

### Episode Validation

Step D improved `4 of 5` evaluation buckets by subperiod return. See:

- `reports/market_wizards/global_macro_barbell_concentration_episode_validation.json`
- `reports/market_wizards/global_macro_barbell_concentration_episode_validation.md`

### Interpretation

Step D works because it concentrates capital only when two things are already true:

- the macro regime is unusually coherent relative to history
- the dislocation book has already produced at least one confirmed Phase-2+ winner

That is a higher-quality pressing rule than broadening exposure or loosening thresholds.
