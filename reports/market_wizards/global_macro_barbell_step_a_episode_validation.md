## Step A Episode Validation

Comparison: `global_macro_barbell_phase_2005_2025_final.json` vs `global_macro_barbell_reallocation_2005_2025_final.json`

Decision basis: subperiod return, not absolute PnL. Later episodes start from different equity bases, so raw PnL alone is not comparable.

Improved episodes by return: `4` of `5`

| Episode | Step 4 return | Step A return | Delta return | Step 4 pnl | Step A pnl | Delta pnl |
|---|---:|---:|---:|---:|---:|---:|
| crisis_2007_2009 | 93.63% | 105.98% | +12.35% | 235,651 | 266,730 | +31,078 |
| transition_2014_2016 | 14.16% | 35.94% | +21.78% | 71,083 | 209,297 | +138,214 |
| shock_2020 | 51.69% | 63.02% | +11.33% | 347,069 | 570,747 | +223,678 |
| shock_2022 | 26.24% | 23.16% | -3.08% | 290,563 | 401,220 | +110,657 |
| all_other_periods | 504.54% | 802.55% | +298.01% | 1,261,360 | 2,006,380 | +745,020 |

### Decision

Step A is broad enough to keep building on.