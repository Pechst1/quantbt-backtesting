## Step 3 Macro Velocity Layer

Window: `2005-01-01` to `2025-01-01`  
Step 2 baseline artifact: `reports/market_wizards/global_macro_barbell_overlay_2005_2025_final.json`  
Explicit Step 3 artifact: `reports/market_wizards/global_macro_barbell_velocity_2005_2025_final.json`

### Decision

Step 3 is implemented, but it is not enabled by default.

Reason:

- the velocity layer is now technically correct and cheap enough to run
- but the tested 20-year sample does not support turning it on in production

The default `global_macro_barbell` path remains the Step 2 strategy.

### What Was Implemented

- point-in-time macro velocity computed from monthly z-score changes
- Engine B velocity sizing multiplier
- Engine B early-exit hook on velocity deceleration
- overlay exit on velocity deceleration instead of fixed time-stop
- cached regional macro state keyed by `(region, macro_snapshot.as_of)` to remove the runtime explosion
- CLI flag to enable the experimental layer:
  - `--barbell-velocity-layer`

### Baseline Integrity Check

With velocity disabled, the current code reproduces the Step 2 baseline exactly:

| Mode | Annualized Return | Sharpe | Max Drawdown | Engine B Net PnL |
|---|---:|---:|---:|---:|
| Default (`velocity off`) | 9.4293% | 0.7175 | -21.0001% | 1,237,340.09 |
| Step 2 historical artifact | 9.4293% | 0.7175 | -21.0001% | 1,237,340.09 |

This matters because it confirms the caching/refactor work did not change the production baseline.

### Best Tested Step 3 Variant

Best velocity-on variant after a small focused sweep:

- `engine_b_velocity_boost_signal = 0.75`
- `engine_b_velocity_boost_multiplier = 1.20`
- `engine_b_velocity_exit_signal = 0.15`
- `engine_b_velocity_peak_floor = 0.75`
- `engine_b_velocity_score_decay = 0.90`

Result:

| Metric | Step 2 | Step 3 On | Delta |
|---|---:|---:|---:|
| Annualized return | 9.4293% | 9.1248% | -0.3045% |
| Sharpe | 0.7175 | 0.6941 | -0.0234 |
| Max drawdown | -21.0001% | -20.9980% | +0.0021% |
| Engine B net PnL | 1,237,340.09 | 1,157,173.46 | -80,166.63 |

### Diagnostics

The velocity layer remained too inactive to justify itself:

| Diagnostic | Value |
|---|---:|
| Engine B velocity signal mean | 0.1391 |
| Engine B velocity multiplier mean | 1.0039 |
| Engine B velocity exit ratio | 0.0596% |
| Overlay velocity exit ratio | 0.0199% |

Interpretation:

- the layer is almost never changing position size materially
- the early-exit logic is almost never firing
- even so, the marginal effect is negative on this sample

### Why It Underperformed

The likely issue is not software correctness anymore. It is signal economics:

- monthly macro velocity in this proxy dataset is too sparse and too weak relative to the already-strong Step 2 barbell
- the barbell already captures most of the profitable regime transition exposure through theme coherence and transmission lag
- the added velocity dimension does not add enough independent information

### Recommended Status

- keep the implementation for research
- keep it off by default
- do not promote it into the production barbell until a different macro dataset or a different velocity construction shows clear out-of-sample improvement

### How To Run It Explicitly

```bash
cd /Users/vincentpechstein/Downloads/Pixel-lab/Backtesting
source .venv/bin/activate

python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-velocity-layer \
  --start 2005-01-01 \
  --end 2025-01-01 \
  --interval 1d \
  --no-report
```
