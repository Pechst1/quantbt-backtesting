## Step 4 Phase-Based Stop Layer

Window: `2005-01-01` to `2025-01-01`  
Step 2 baseline: `reports/market_wizards/global_macro_barbell_overlay_2005_2025_final.json`  
Step 3 explicit velocity run: `reports/market_wizards/global_macro_barbell_velocity_2005_2025_final.json`  
Step 4 artifact: `reports/market_wizards/global_macro_barbell_phase_2005_2025_final.json`

### Decision

Step 4 is the first experimental extension that materially improves the return profile.

It should remain an explicit research mode for now, but it is the strongest path tested so far.

### What Was Added

- phase-based sizing for Engine B core positions
  - Phase 1: half-size starter
  - Phase 2: full size
  - Phase 3: pressed size
- phase-based stop widths
  - Phase 1: tight entry stop
  - Phase 2 and 3: wider trailing stop
- overlay phase logic
  - overlay starts half-size
  - can scale only after lag starts closing
  - can be removed if lag fails or phase stop triggers
- velocity and coherence gating for phase advancement

### Results

| Metric | Step 2 | Step 3 | Step 4 |
|---|---:|---:|---:|
| Annualized return | 9.4293% | 9.1248% | 11.4420% |
| Sharpe | 0.7175 | 0.6941 | 0.8406 |
| Max drawdown | -21.0001% | -20.9980% | -23.9957% |
| Profit factor | 2.2257 | 2.1315 | 4.0650 |
| Closed trades | 572 | 575 | 569 |

### Step 4 vs Step 2

| Metric | Delta |
|---|---:|
| Annualized return | +2.0128% |
| Sharpe | +0.1231 |
| Max drawdown | -2.9956% |
| Profit factor | +1.8393 |
| Engine A net PnL | +8,566.57 |
| Engine B net PnL | +594,564.85 |

### Key Diagnostic Point

The improvement did not come from simply using more capital.

Engine B average gross exposure:

- Step 2: higher
- Step 4: lower by about `0.0272`

That means the gain came from payoff reshaping:

- smaller initial losers
- faster cutting of early failures
- larger participation only after trades proved themselves

### Engine B Diagnostics

| Diagnostic | Value |
|---|---:|
| Engine B average gross | 0.3481 |
| Engine B phase mean | 0.5834 |
| Engine B phase 3 ratio | 3.1989% |
| Engine B phase stop ratio | 16.2726% |
| Engine B trade win rate | 63.36% |
| Engine B trade profit factor | 4.3163 |

Interpretation:

- most of the time, positions stayed in low or neutral phase
- only a small fraction of days reached Phase 3
- that small fraction was enough to change the total payoff profile materially

### Why This Worked Better Than Step 3

Step 3 on its own was too weak because velocity added little direct signal edge.

Step 4 uses velocity differently:

- not as a primary alpha source
- but as a throttle for when to press or hold size

That is a better use of the information.

### Recommended Status

- keep Step 2 as the conservative default
- treat Step 4 as the current best research variant
- do not promote it to the default production path until it passes additional robustness checks

### How To Run Step 4

```bash
cd /Users/vincentpechstein/Downloads/Pixel-lab/Backtesting
source .venv/bin/activate

python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-velocity-layer \
  --barbell-phase-stop-layer \
  --start 2005-01-01 \
  --end 2025-01-01 \
  --interval 1d \
  --no-report
```
