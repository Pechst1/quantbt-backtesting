# TopDownGlobalMacroWizardStrategy: Structural Adjustment Pass

## Scope

This note supersedes the earlier macro report for code-level behavior.

Implemented changes:

- continuous conviction sizing above asset-specific exit thresholds
- asset-class-specific entry / exit thresholds
- asset-class-specific trend filters
- monthly direction planning with weekly size updates
- carry layer for quiet / dead-band regimes in `HYG` and `LQD`
- `MIXED` theme shifted toward carry + relative expressions instead of inactivity
- expanding-window z-score normalization with a volatility floor
- resize hysteresis to suppress micro-rebalances

Files:

- strategy: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/strategies/market_wizards.py`
- tests: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/tests/test_macro_strategy.py`
- final diagnostics: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_2005_2025_adjusted_final_diagnostics.json`


## Before / After

Baseline from the prior report:

- annualized return: `2.3678%`
- Sharpe: `0.5638`
- max drawdown: `-16.97%`
- closed trades: `331`
- average gross exposure: `17.53%`

Adjusted strategy:

- annualized return: `1.2798%`
- Sharpe: `0.2723`
- max drawdown: `-12.62%`
- closed trades: `523`
- average gross exposure: `21.16%`


## What Improved

1. Deployment increased.
   - average gross exposure moved from `17.5%` to `21.2%`
   - flat-time reduced, but not enough

2. Turnover is materially lower than the first non-hysteresis adjustment pass.
   - first pass: `1207` closed trades
   - final hysteresis pass: `523` closed trades

3. Drawdown improved.
   - `-16.97%` to `-12.62%`


## What Did Not Improve

The main objective, higher `annualized_return`, was not achieved.

Why:

1. The new capital deployment went into lower-quality marginal trades.
2. `MIXED`-regime relative activity and added weekly resizing increased activity faster than they increased edge.
3. The strongest sleeves remain concentrated, but the new logic still leaks capital into weak or mediocre sleeves.


## Final Diagnostics Read-Through

Top contributors after the adjustment pass:

- `USO`: `+22.87k`
- `LQD`: `+15.74k`
- `DBC`: `+12.91k`
- `EWJ`: `+11.69k`
- `HYG`: `+11.35k`
- `EFA`: `+10.86k`
- `SPY`: `+8.99k`

Weak sleeves after the adjustment pass:

- `TLT`: `-6.12k`
- `GLD`: `-0.75k`
- `IEF`: `-0.61k`
- `TIP`: `-0.35k`
- `EEM`: roughly flat

Interpretation:

- the carry / credit layer works directionally
- commodities remain useful
- rates are still a drag
- gold / TIPS still do not justify frequent participation
- the additional signal density is not yet selective enough


## Critical Conclusion

The user’s diagnosis was correct on structure:

- the old strategy had a deployment problem
- monthly binary gating was too sparse

But the first practical lesson from implementation is:

- increasing time invested is not sufficient
- the marginal additional exposure must be concentrated in the best sleeves

The current adjusted strategy is structurally better engineered, but economically worse on CAGR.


## Next Optimization Direction

If the objective is explicitly higher `annualized_return`, the next pass should:

1. keep continuous sizing
2. keep carry in credit
3. keep asset-specific trend filters
4. reduce or disable directional rate exposure in `TLT` / `IEF`
5. hard-cap or disable `GLD` and `TIP` except in extreme regimes
6. tighten `MIXED` relative overlays so they require stronger cross-country differentials

That is the highest-probability path to turn the structural improvements into actual return improvement.
