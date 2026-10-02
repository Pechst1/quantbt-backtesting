## Step 2 Transmission Lag Overlay

Window: `2005-01-01` to `2025-01-01`  
Baseline for comparison: `reports/market_wizards/global_macro_barbell_feedback_2005_2025_final.json`  
Step 2 artifact: `reports/market_wizards/global_macro_barbell_overlay_2005_2025_final.json`

### Decision

Keep Step 2.

The overlay improved return quality without paying for it in drawdown. The change is economically additive, not just more activity.

### Implementation Summary

- Leader signal uses:
  - Engine A trailing 5-day PnL z-score
  - `UUP` standardized 10-day move
- Overlay universe:
  - `SPY`, `EEM`, `DBC`, `USO`
- Activation threshold:
  - 75th percentile of the symbol's own historical leader score
  - clipped to `[0.60, 1.20]`
- Lag score:
  - `leader_score * (1 - move / leader_score)`
- Holding limits:
  - `SPY`: 20 trading days
  - `EEM`: 25 trading days
  - `DBC`: 20 trading days
  - `USO`: 15 trading days
- Overlay size:
  - capped to 50% of the core Engine B weight
  - capped again by residual per-asset exposure room

### Step 1 vs Step 2

| Metric | Step 1 | Step 2 | Delta |
|---|---:|---:|---:|
| Annualized return | 8.8932% | 9.4293% | +0.5361% |
| Sharpe | 0.6902 | 0.7175 | +0.0273 |
| Sortino | 0.8182 | 0.8602 | +0.0420 |
| Max drawdown | -21.0007% | -21.0001% | +0.0006% |
| Calmar | 0.4235 | 0.4490 | +0.0255 |
| Win rate | 54.21% | 54.72% | +0.51% |
| Profit factor | 2.0615 | 2.2257 | +0.1642 |
| Closed trades | 546 | 572 | +26 |
| Submitted orders | 1456 | 1539 | +83 |
| Risk rejections | 21 | 28 | +7 |

### Sleeve Attribution

| Sleeve | Step 1 Net PnL | Step 2 Net PnL | Delta |
|---|---:|---:|---:|
| Engine A | 71,488.74 | 71,100.72 | -388.02 |
| Engine B | 1,095,830.70 | 1,237,340.09 | +141,509.39 |

Interpretation:

- The gain came almost entirely from Engine B, which is the intended behavior.
- Engine A remained effectively unchanged, which means the overlay did not distort the carry sleeve.

### Overlay Diagnostics

| Diagnostic | Value |
|---|---:|
| Overlay active ratio | 3.8943% |
| Average active overlay count | 0.0703 |
| Average leader score | 0.0188 |
| Average lag score | 0.0141 |
| Average threshold | 0.7696 |
| Average active overlay weight | 0.4740% |

Interpretation:

- The overlay was active rarely.
- When active, it was small.
- Despite that, it added meaningful PnL to Engine B.

This is exactly the profile we want from a dislocation monetization layer: sparse, selective, additive.

### Critical Assessment

What worked:

- The refined leader signal from Engine A PnL plus `UUP` is cleaner than using raw credit ETF returns.
- The percentile thresholding prevented the overlay from becoming another always-on risk source.
- Asset-specific holding windows avoided forcing a one-size-fits-all transmission horizon.

What did not materially change:

- Engine B dormant ratio is still structurally high.
- The overlay improves monetization per episode, but it does not solve timing by itself.

### Implication For Step 3

The next valid move remains macro velocity, not broader deployment.

Reason:

- Step 2 improved episode extraction.
- The remaining problem is earlier activation and earlier profit-taking around regime acceleration and deceleration.

That matches the Step 3 design goal directly.
