## Step B Asymmetric Crisis-Expression Phase Timing

Window: `2005-01-01` to `2025-01-01`  
Baseline: `reports/market_wizards/global_macro_barbell_reallocation_2005_2025_final.json`  
Step B artifact: `reports/market_wizards/global_macro_barbell_asymmetric_2005_2025_final.json`

### Decision

Do not keep Step B as the new research baseline.

The asymmetry logic is economically sensible, but on the current dataset it did not improve the Step A profile.

### What Was Added

- crisis-speed expressions advance from Phase 1 to Phase 2 faster than slower risk-on expressions
- fast crisis expressions are defined as:
  - short `SPY`, `EEM`, `DBC`, `USO`
  - long `UUP`
- only the first phase transition was accelerated:
  - default: `10` bars and `1.0 ATR`
  - crisis-speed: `5` bars and `0.75 ATR`
- Step B runs on top of Step A, not instead of it

### Results

| Metric | Step A | Step B |
|---|---:|---:|
| Annualized return | 11.6476% | 11.5759% |
| Sharpe | 0.8471 | 0.8423 |
| Sortino | 1.0015 | 0.9952 |
| Max drawdown | -23.9935% | -23.9903% |
| Profit factor | 4.0946 | 4.0570 |
| Closed trades | 563 | 568 |
| Submitted orders | 1642 | 1650 |
| Risk rejections | 12 | 12 |

### Step B vs Step A

| Metric | Delta |
|---|---:|
| Annualized return | -0.0717% |
| Sharpe | -0.0048 |
| Sortino | -0.0063 |
| Max drawdown | +0.0032% |
| Profit factor | -0.0375 |
| Engine A net PnL | -855.71 |
| Engine B net PnL | -27,156.85 |

### Interpretation

The accelerated Phase 1 -> Phase 2 promotion did not add enough monetization to outweigh the extra churn and altered payoff timing.

Most likely reasons:

- the current crisis-speed classification is directionally correct but still too coarse
- advancing earlier increased exposure before enough price confirmation had accumulated
- the strongest Step 4 edge still appears to come from payoff shaping after confirmation, not earlier promotion into size

### Recommendation

- keep Step A as the active research baseline
- do not stack Step B further into production research for now
- if Step B is revisited later, do it with episode-specific attribution and stricter crisis gating rather than a broad always-on asymmetry rule
