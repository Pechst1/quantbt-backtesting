# Global Macro Barbell: Inter-Engine Feedback Pass

## Scope

This pass implements only Step 1 from the proposed enhancement stack:

- Engine A trailing PnL now modifies Engine B activation thresholds
- stress in Engine A lowers the short activation threshold
- euphoria in Engine A lowers the long activation threshold

Files:

- strategy: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/strategies/market_wizards.py`
- new run artifact: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_feedback_2005_2025_final.json`
- prior barbell baseline: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_2005_2025_final.json`

## Decision

Keep this change.

It improves the profile on the metrics that matter most for the current stage:

- higher annualized return
- slightly higher Sharpe and Sortino
- unchanged max drawdown
- higher Engine B net PnL

## Before / After

Baseline barbell:

- annualized return: `8.6992%`
- Sharpe: `0.6837`
- Sortino: `0.8039`
- max drawdown: `-21.00%`
- Calmar: `0.4142`
- closed trades: `514`
- Engine B net PnL: `1,044,530`
- Engine B dormant ratio: `47.61%`

Feedback pass:

- annualized return: `8.8932%`
- Sharpe: `0.6902`
- Sortino: `0.8182`
- max drawdown: `-21.00%`
- Calmar: `0.4235`
- closed trades: `546`
- Engine B net PnL: `1,095,831`
- Engine B dormant ratio: `46.67%`

## Read-Through

What improved:

1. Engine B activated slightly earlier and slightly more often.
   - dormant ratio moved from `47.61%` to `46.67%`
   - average gross exposure rose from `36.45%` to `37.58%`

2. Engine B PnL increased materially.
   - `+51.3k` absolute improvement versus the barbell baseline

3. The additional activation did not worsen drawdown.
   - max drawdown remained effectively unchanged at `-21.00%`

What worsened:

1. Trade count increased.
   - `514` to `546` closed trades

2. Risk rejections increased.
   - `14` to `21`

3. Profit factor dipped slightly.
   - `2.0719` to `2.0615`

Interpretation:

- the threshold-feedback layer is additive, but only modestly so
- this is a good sign for the next step, because it means the core hypothesis is right without destabilizing the strategy
- the main benefit is timing, not a radically different return engine

## Feedback Diagnostics

Observed behavior of the new signal layer:

- Engine A stress state active on `28.11%` of observed days
- Engine A euphoria state active on `20.68%` of observed days
- average long threshold multiplier: `0.9586`
- average short threshold multiplier: `0.9157`

This is about the right order of magnitude:

- the feedback is active often enough to matter
- it is not active so often that it effectively collapses back into a permanently loose threshold model

## Conclusion

This step should stay in the strategy.

It is not the full CAGR jump we want, but it clears the bar for incremental acceptance:

- better annualized return
- better risk-adjusted return
- no extra drawdown cost

That makes the next step clear:

- implement the transmission lag overlay using Engine A trailing PnL plus `UUP` as the leader signal
- keep the rest of the barbell unchanged so the overlay effect is measurable in isolation
