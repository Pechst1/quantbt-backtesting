# Global Macro Barbell Report (2005-01-01 to 2025-01-01)

## Scope

This report compares the new `BarbellMacroWizardStrategy` against the previously adjusted single-engine `TopDownGlobalMacroWizardStrategy`.

Files:

- barbell strategy: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/src/quantbt/strategies/market_wizards.py`
- barbell run metrics: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_2005_2025_final.json`
- adjusted single-engine baseline: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_2005_2025_adjusted_final_diagnostics.json`

## Headline Result

The barbell architecture is materially better than the adjusted single-engine implementation.

It solves the actual economic problem revealed by the prior report:

- the carry sleeve keeps capital productively deployed during quiet regimes
- the dislocation sleeve stays dormant almost half the time, but when active it is concentrated enough to matter
- the result is higher CAGR with a still-manageable drawdown profile

## Before / After

Adjusted single-engine baseline:

- annualized return: `1.2798%`
- Sharpe: `0.2723`
- max drawdown: `-12.62%`
- closed trades: `523`
- average gross exposure: `21.16%`
- average absolute net exposure: `18.62%`

Barbell strategy:

- annualized return: `8.6992%`
- Sharpe: `0.6837`
- Sortino: `0.8039`
- max drawdown: `-21.00%`
- Calmar: `0.4142`
- closed trades: `514`
- submitted orders: `1396`
- fills: `1396`
- risk rejections: `14`

Interpretation:

- CAGR improved by roughly `+742 bps`
- Sharpe improved materially
- drawdown increased, but the return improvement is large enough that the trade-off is economically justified
- turnover is similar to the adjusted single-engine run, which means the gain is coming from better deployment quality, not just more trading

## Engine Split

### Engine A: Credit Carry / Beta Sleeve

Universe:

- `HYG`
- `LQD`

Mechanics:

- fixed target weights when active
- explicit crisis shutoff budget
- shutoff triggers:
  - `HYG` 20-day drawdown breach
  - credit surprise z-score breach
  - trailing 60-day Engine A PnL budget breach
- re-entry starts at half size and scales back over 4 weeks

Observed performance:

- closed trades: `110`
- net PnL: `68,969`
- win rate: `57.27%`
- profit factor: `2.25`
- average gross exposure: `19.97%`
- shutoff ratio: `20.33%` of observed days
- final sleeve PnL: `117,816`

Interpretation:

- Engine A does what it is supposed to do
- it is not a dominant return source, but it provides a stable return floor and keeps the portfolio from idling during non-dislocation regimes
- the explicit shutoff logic is active often enough to matter, which validates the separate drawdown-budget framing

### Engine B: Macro Dislocation Sleeve

Universe:

- `SPY`
- `EEM`
- `DBC`
- `USO`
- `UUP`

Mechanics:

- concentrated cross-asset macro expressions only when dislocation strength is high
- coherence-adjusted macro scoring
- dormant below threshold
- top `2-3` expressions only
- trailing stop still enforced on active positions

Observed performance:

- closed trades: `404`
- net PnL: `1,044,530`
- win rate: `53.22%`
- profit factor: `2.06`
- average gross exposure: `36.45%`
- dormant ratio: `47.61%` of observed days
- average max score: `1.0569`
- final sleeve PnL: `1,035,320`

Interpretation:

- Engine B is the dominant alpha source, exactly as hypothesized
- the strategy is still dormant nearly half the time, which is consistent with the claim that this is a dislocation detector rather than a continuous macro allocator
- when active, it is concentrated enough to generate meaningful portfolio-level returns

## Critical Read-Through

The earlier adjusted strategy failed because it tried to monetize weak signals more often.

The barbell strategy works because it does the opposite:

- low-conviction time is allocated to a separate carry sleeve with its own risk budget
- high-conviction time is concentrated in a smaller, cleaner dislocation universe
- the dislocation sleeve is allowed to remain dormant instead of being forced to justify its existence in quiet periods

That is the correct structural response to the return series.

## Remaining Risks

1. Drawdown is higher.
   - `-21.00%` is acceptable relative to `8.70%` CAGR, but this is now a true growth strategy rather than a low-vol overlay.

2. Engine B dominates total PnL.
   - the barbell is not balanced in contribution terms.
   - that is not automatically bad, but it means future optimization should focus primarily on Engine B quality control.

3. Product proxies still matter.
   - `USO` and `DBC` are ETF implementations, not futures.
   - some of the result depends on ETF mechanics, especially roll behavior.

## Next Steps

Highest-value next changes:

1. Add sleeve-level attribution by symbol for Engine B.
   - identify whether the return is dominated by `USO`, `DBC`, `SPY`, or `UUP`

2. Add the policy-reversal circuit breaker to Engine B.
   - this is the cleanest next defense against 2019-style whipsaws

3. Add the vol-of-vol early activation trigger.
   - that should improve crisis timing without making the engine continuously invested

4. Keep pyramiding out of scope for now.
   - the current result is already good enough to justify a second pass
   - adding press logic too early would make diagnosis harder

## Verdict

The barbell architecture should replace the single-engine macro strategy as the primary macro template in this codebase.

Not because it is cosmetically cleaner, but because the backtest now supports the economic thesis:

- Engine A monetizes normal regimes without pretending to be macro alpha
- Engine B monetizes regime breaks with enough concentration to matter
- the combined result is the first macro implementation in this repository that reaches a plausibly useful return profile
