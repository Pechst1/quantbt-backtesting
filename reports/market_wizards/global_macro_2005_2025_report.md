# TopDownGlobalMacroWizardStrategy

## Executive Summary

This strategy is now implemented as a point-in-time, event-driven macro allocation model expressed through liquid ETF proxies:

- Equities: `SPY`, `EFA`, `EEM`, `EWJ`
- Rates: `TLT`, `IEF`, `TIP`
- Commodities / real assets: `GLD`, `USO`, `DBC`
- FX / credit: `UUP`, `HYG`, `LQD`

Backtest window:

- Engine window: `2005-01-03` to `2024-12-31`
- Macro-active window: `2005-01-21` to `2024-12-20`

Headline result:

- Full-window annualized return: `2.37%`
- Full-window Sharpe: `0.56`
- Full-window max drawdown: `-16.97%`
- Closed trades: `331`

Interpretation:

- The strategy is now structurally credible as a macro engine.
- It is not yet optimized for return density.
- The dominant limitation is not drawdown control; it is under-deployment of capital and weak sleeves that dilute the strongest expressions.

For a quant strategist optimizing for higher `annualized_return`, the highest-impact path is:

1. Increase capital efficiency without breaking drawdown.
2. Reweight or disable weak sleeves (`GLD`, `TIP`, marginally `EFA`).
3. Recalibrate theme-to-expression mapping and dead-band thresholds.
4. Make adaptive weighting more selective, not always-on.


## Strategy Specification

Current implementation:

- Monthly target-position rebalance.
- Daily trailing-stop monitoring.
- Point-in-time macro snapshots with `release_date` and `tradable_from`.
- 36-month rolling z-scores on:
  - `growth_surprise`
  - `inflation_surprise`
  - `policy_surprise`
  - `liquidity_surprise`
  - `credit_surprise`
- Entry threshold: `0.50`
- Exit threshold: `0.20`
- Entry trend filter:
  - long only if `close > SMA200`
  - short only if `close < SMA200`
- ATR risk sizing with score-scaled risk budget.
- Theme mapping:
  - `REFLATION`
  - `DISINFLATION`
  - `STAGFLATION`
  - `GOLDILOCKS`
  - `MIXED`
- Relative overlays:
  - `US vs EUROPE`
  - `US vs JAPAN`
  - `US vs EM`
- Internal exposure gating:
  - `max_gross_total = 2.5`
  - `max_net_total = 1.2`
  - `max_gross_per_region = 0.40`
  - `max_gross_per_asset_class = 0.30`
  - `max_theme_expressions = 3`
- Trailing stop:
  - `3 * ATR`


## Performance Summary

### Full Window

| Metric | Value |
|---|---:|
| Annualized Return | `2.3678%` |
| Sharpe Ratio | `0.5638` |
| Sortino Ratio | `0.5304` |
| Maximum Drawdown | `-16.9723%` |
| Calmar Ratio | `0.1395` |
| Win Rate | `63.75%` |
| Profit Factor | `1.9672` |
| Closed Trades | `331` |
| Max Drawdown Duration | `2968` days |

### Macro-Active Window

This matters because the strategy should be judged on the period where macro signals are actually tradable, not just on the raw engine window.

| Metric | Value |
|---|---:|
| Active Window Start | `2005-01-21` |
| Active Window End | `2024-12-20` |
| Annualized Return | `2.3745%` |
| Sharpe Ratio | `0.5649` |
| Maximum Drawdown | `-16.9723%` |
| Average Gross Exposure | `17.59%` |
| Average Absolute Net Exposure | `15.40%` |

Key implication:

- The strategy is extremely lightly invested on average.
- Return is low primarily because exposure is low, not because the trade hit rate is poor.


## Drawdown Profile

Worst drawdown episode:

- Equity peak: `2016-01-20`
- Trough: `2020-01-08`
- Recovery: `2024-03-07`
- Max drawdown: `-16.97%`

Interpretation:

- This is a long, slow capital-efficiency problem more than a crash-loss problem.
- The strategy avoided catastrophic losses, but the recovery time is too long relative to the modest CAGR.
- That combination is usually a sign of insufficient upside capture during macro regimes rather than excessive risk-taking.


## Exposure Diagnostics

| Metric | Value |
|---|---:|
| Average Gross Exposure | `17.53%` |
| Average Absolute Net Exposure | `15.34%` |
| 95th Percentile Gross Exposure | `45.41%` |
| 95th Percentile Absolute Net Exposure | `45.28%` |

This is the single most important optimization clue in the entire report.

The strategy is running with:

- portfolio-level risk controls that are not binding
- `0` risk rejections
- no margin calls
- but average gross exposure below `20%`

Conclusion:

- The current model is too selective and too sparse.
- It is leaving too much of the capital stack idle.


## Trade Activity

| Engine Stat | Value |
|---|---:|
| Submitted Orders | `634` |
| Fills | `634` |
| Risk Rejections | `0` |
| Margin Calls | `0` |

Trade timing:

- First closed trade: `2007-02-28`
- Last closed trade: `2024-12-19`
- End-of-test state: flat, no open positions


## Instrument Attribution

### Net PnL by Instrument

| Symbol | Closed Trades | Net PnL | Avg PnL / Trade | Win Rate | Profit Factor |
|---|---:|---:|---:|---:|---:|
| `DBC` | 34 | `30,489.93` | `896.76` | `61.76%` | `2.69` |
| `EEM` | 40 | `26,574.50` | `664.36` | `65.00%` | `2.30` |
| `USO` | 31 | `25,584.44` | `825.30` | `45.16%` | `1.49` |
| `LQD` | 34 | `23,450.47` | `689.72` | `82.35%` | `9.00` |
| `HYG` | 48 | `21,590.17` | `449.80` | `83.33%` | `3.15` |
| `SPY` | 40 | `14,704.01` | `367.60` | `70.00%` | `1.95` |
| `EWJ` | 31 | `10,649.35` | `343.53` | `51.61%` | `1.70` |
| `IEF` | 16 | `4,489.55` | `280.60` | `43.75%` | `2.45` |
| `TLT` | 20 | `3,633.69` | `181.68` | `55.00%` | `1.35` |
| `EFA` | 26 | `2,094.09` | `80.54` | `61.54%` | `1.16` |
| `TIP` | 2 | `-508.03` | `-254.01` | `50.00%` | `0.11` |
| `GLD` | 9 | `-1,917.41` | `-213.05` | `33.33%` | `0.63` |

### Attribution Read-Through

Best sleeves:

- `DBC`
- `EEM`
- `USO`
- `LQD`
- `HYG`
- `SPY`

Weak sleeves:

- `GLD`
- `TIP`
- `EFA`

Implications for return optimization:

1. Commodity beta is valuable when expressed through broad commodities and oil, but not through gold in the current mapping.
2. Credit sleeves are the cleanest winners in the strategy.
3. Developed ex-US equity (`EFA`) is low-value capital usage at current signal logic.
4. Inflation-linked bonds (`TIP`) are too sparse and negative-expectancy to justify allocation in v1.


## Trade Count Concentration

| Symbol | Closed Trades |
|---|---:|
| `HYG` | 48 |
| `SPY` | 40 |
| `EEM` | 40 |
| `LQD` | 34 |
| `DBC` | 34 |
| `EWJ` | 31 |
| `USO` | 31 |
| `EFA` | 26 |
| `TLT` | 20 |
| `IEF` | 16 |
| `GLD` | 9 |
| `TIP` | 2 |

This tells you where the model is actually finding signal persistence:

- credit
- EM beta
- commodity beta
- US/Japan equity expressions

That is where further optimization effort should be concentrated first.


## Return Path Diagnostics

### Yearly Returns

| Year | Return |
|---|---:|
| 2006 | `0.00%` |
| 2007 | `-1.75%` |
| 2008 | `27.83%` |
| 2009 | `0.15%` |
| 2010 | `2.65%` |
| 2011 | `0.00%` |
| 2012 | `0.00%` |
| 2013 | `0.00%` |
| 2014 | `5.25%` |
| 2015 | `7.71%` |
| 2016 | `-3.56%` |
| 2017 | `0.19%` |
| 2018 | `1.40%` |
| 2019 | `-9.34%` |
| 2020 | `11.66%` |
| 2021 | `-0.68%` |
| 2022 | `4.80%` |
| 2023 | `1.88%` |
| 2024 | `3.66%` |

### Monthly Return Distribution

| Metric | Value |
|---|---:|
| Mean Monthly Return | `0.204%` |
| Median Monthly Return | `0.000%` |
| Monthly Std Dev | `1.296%` |
| Best Month | `9.60%` |
| Worst Month | `-3.31%` |
| Positive Months | `36.40%` |

Interpretation:

- Median monthly return at exactly `0.00%` is a problem.
- The strategy has too many flat months.
- That confirms the under-deployment diagnosis.

### Rolling 252-Day Diagnostics

| Metric | Value |
|---|---:|
| Best 252-Day Annualized Return | `30.73%` |
| Worst 252-Day Annualized Return | `-10.36%` |
| Best 252-Day Sharpe | `2.87` |
| Worst 252-Day Sharpe | `-2.29` |

Interpretation:

- The model can produce good 1-year stretches.
- The problem is not lack of edge in every regime.
- The problem is unstable regime capture and insufficient continuity.


## What Is Working

1. Risk is controlled.
   - No risk rejections.
   - No margin calls.
   - Drawdown is moderate for a macro strategy.

2. The credit sleeves are strong.
   - `LQD` and `HYG` are among the highest-quality signal expressions.

3. Commodity complex is useful.
   - `DBC` and `USO` are major positive contributors.

4. Relative-country logic is directionally useful.
   - `EEM`, `EWJ`, and `SPY` all contribute positively.


## What Is Not Working

1. Capital is underused.
   - Average gross exposure is only `17.5%`.
   - This is the main drag on CAGR.

2. Some sleeves are negative-expectancy.
   - `GLD`
   - `TIP`

3. Some sleeves are low-productivity.
   - `EFA` is consuming activity for very low net output.

4. The dead-band and entry gating are still too conservative for a return-maximization objective.
   - Median monthly return is zero.
   - Several years are near-flat.


## Optimization Priorities

### Priority 1: Raise Gross Exposure Intelligently

Goal:

- increase annualized return without letting drawdown scale linearly

Evidence:

- `avg_gross_exposure = 17.5%`
- `p95_gross_exposure = 45.4%`
- `0` risk rejections

Concrete actions:

1. Increase `risk_pct` from `0.75%` to `1.00%` or `1.25%`.
2. Increase `max_notional_pct` from `0.15` to `0.20`.
3. Increase `max_theme_expressions` from `3` to `4`.
4. Relax `max_gross_per_asset_class` from `0.30` to `0.40` for `CREDIT` and `COMMODITY`.

Expected effect:

- highest direct path to higher CAGR
- likely modest Sharpe degradation, but currently the strategy has room

### Priority 2: Remove or Downweight Weak Sleeves

Evidence:

- `GLD`: negative PnL, `PF 0.63`
- `TIP`: negative PnL, `PF 0.11`
- `EFA`: `PF 1.16`, very low average trade value

Concrete actions:

1. Disable `TIP` entirely in v2.
2. Disable `GLD` for `REFLATION` and `GOLDILOCKS`; keep only for `STAGFLATION` / `DISINFLATION` stress use-cases.
3. Reduce `EFA` sizing by `50%` unless relative score exceeds a higher threshold than `SPY` / `EEM`.

Expected effect:

- better capital concentration
- improved annualized return and likely improved PF

### Priority 3: Separate Fast and Slow Expressions

Problem:

- credit and commodities are working, but they respond faster than duration and some equity sleeves

Concrete actions:

1. Use shorter exit dead-band for `USO`, `DBC`, `HYG`, `EEM`.
2. Keep wider dead-band only for `LQD`, `IEF`, `TLT`.
3. Use asset-specific trailing stop multipliers instead of a universal `3 * ATR`.

Expected effect:

- better upside retention in fast-moving macro themes
- less delayed exit in commodities

### Priority 4: Make Adaptive Betas Conditional

Current state:

- adaptive weighting is shrunk to templates, which fixed the prior shutdown issue
- but it still runs continuously

Concrete actions:

1. Only enable adaptive betas when rolling regression `R^2` clears a threshold.
2. Otherwise revert fully to exposure templates.
3. Consider separate calibration windows by asset class:
   - `CREDIT`: 36 to 48 months
   - `COMMODITY`: 24 to 36 months
   - `RATES`: 48 to 60 months

Expected effect:

- less regime-misaligned weighting
- more stable return capture

### Priority 5: Recalibrate Thresholds for Higher Signal Density

Current:

- entry `0.50`
- exit `0.20`

Suggested grid:

- entry: `0.35`, `0.40`, `0.50`
- exit: `0.10`, `0.15`, `0.20`
- trend filter: `SMA150` vs `SMA200`

Why:

- current thresholds leave too many flat months
- strategy needs more continuous deployment

Expected effect:

- more trades
- higher utilization
- likely higher CAGR if combined with sleeve pruning


## Recommended Optimization Sequence

Run these in order:

1. Disable `TIP` and restrict `GLD`.
2. Raise `risk_pct` and `max_notional_pct`.
3. Relax thresholds to increase signal density.
4. Add asset-specific stop / exit logic.
5. Make adaptive weights conditional on regression quality.

Do not start with:

- adding more instruments
- adding more macro factors
- adding leverage

The current evidence says the first-order issue is allocation efficiency inside the existing signal stack, not feature scarcity.


## Suggested Experiment Matrix

### Experiment A: Return Density

- `risk_pct`: `0.75%`, `1.00%`, `1.25%`
- `max_notional_pct`: `0.15`, `0.20`, `0.25`

Primary objective:

- maximize `annualized_return`

Guardrails:

- `max_drawdown <= 22%`
- `Sharpe >= 0.50`

### Experiment B: Sleeve Pruning

- base universe
- base minus `TIP`
- base minus `TIP` and `GLD`
- base minus `TIP`, `GLD`, and reduced `EFA`

Primary objective:

- improve CAGR and PF simultaneously

### Experiment C: Threshold Density

- entry / exit:
  - `0.50 / 0.20`
  - `0.40 / 0.15`
  - `0.35 / 0.10`

Primary objective:

- reduce flat months
- raise average gross exposure

### Experiment D: Exit Logic

- uniform trailing stop
- asset-class-specific trailing stops
- dead-band plus time stop for commodities / credit

Primary objective:

- improve return persistence after strong signal capture


## Bottom Line

The strategy is now credible as a macro backtest, but it is not yet a high-CAGR allocator.

Current profile:

- good hit rate
- acceptable drawdown
- low capital deployment
- several profitable sleeves
- several weak or unnecessary sleeves

The fastest route to higher annualized return is not more complexity. It is:

1. use more of the allowed risk budget
2. cut weak expressions
3. increase signal density
4. tune exits by asset class


## Files

- Diagnostics JSON: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_2005_2025_diagnostics.json`
- This report: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_2005_2025_report.md`
