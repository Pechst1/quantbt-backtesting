## Step A Cross-Engine Reallocation Layer

Window: `2005-01-01` to `2025-01-01`  
Baseline: `reports/market_wizards/global_macro_barbell_phase_2005_2025_final.json`  
Step A artifact: `reports/market_wizards/global_macro_barbell_reallocation_2005_2025_final.json`

### Decision

Keep Step A.

It improves CAGR and Sharpe over the current Step 4 research baseline without worsening max drawdown in a meaningful way.

### What Was Added

- when Engine A is in crisis shutoff, its freed carry gross is treated as available capital
- that freed gross is reallocated only to existing Engine B positions that have already reached Phase 2 or Phase 3
- reallocation is pro-rata to existing confirmed Engine B core weights
- reallocation does not create new positions and does not override existing velocity or phase exits
- the layer is experimental and only activates when the phase architecture is enabled

### Results

| Metric | Step 4 baseline | Step A |
|---|---:|---:|
| Annualized return | 11.4420% | 11.6476% |
| Sharpe | 0.8406 | 0.8471 |
| Sortino | 0.9945 | 1.0015 |
| Max drawdown | -23.9957% | -23.9935% |
| Profit factor | 4.0650 | 4.0946 |
| Closed trades | 569 | 563 |
| Submitted orders | 1658 | 1642 |
| Risk rejections | 10 | 12 |

### Step A vs Step 4

| Metric | Delta |
|---|---:|
| Annualized return | +0.2056% |
| Sharpe | +0.0065 |
| Sortino | +0.0069 |
| Max drawdown | +0.0022% |
| Profit factor | +0.0296 |
| Engine A net PnL | +1,736.03 |
| Engine B net PnL | +78,717.97 |

### Reallocation Diagnostics

| Diagnostic | Value |
|---|---:|
| Engine A shutoff ratio | 20.3258% |
| Reallocation active ratio | 2.5233% |
| Mean reallocated gross | 0.0072 |
| Avg reallocated symbol count | 0.0449 |
| Engine B phase 3 ratio | 3.1989% |
| Engine B average gross | 0.3490 |

### Interpretation

Step A works through capital routing, not signal expansion.

The improvement came from giving more weight to positions that had already:

- survived Phase 1 filtering
- advanced into confirmed Phase 2 or Phase 3 states
- remained live after velocity and phase-stop risk controls

That is the right shape of improvement for this strategy family.

### Limits

- risk rejections increased, so some of the theoretical freed carry gross could not be deployed
- reallocation was active only in a small minority of bars, which is expected because it requires both Engine A shutoff and confirmed Engine B positions
- this is still a single-sample result until it is decomposed by regime episode

### Recommendation

- keep Step A as the next research baseline on top of Step 4
- before Step B, decompose performance by major dislocation episode to confirm that the uplift is not concentrated in only one event
- if Step A remains robust episode-by-episode, proceed to asymmetric crisis-expression phase timing next
