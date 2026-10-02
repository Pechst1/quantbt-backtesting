# Global Macro Barbell: Overlay Routing Layer

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_overlayroute_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_overlayroute_diagnostics.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_overlayroute_attribution.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_overlayroute_episode_validation.json`

## Change
Attempted to convert the transmission overlay from additive sizing into capital routing.

Routing policy implemented:
- recipient remains the lagging slow-expression signal (`SPY`, `EEM`, `DBC`, `USO`)
- donor pool prefers weaker non-Phase-3 Engine B positions in the same direction
- if no same-direction donor exists, it can fall back to weaker opposite-direction non-Phase-3 positions
- routed capital reduces donor `core_target_weight` and increases recipient `overlay_target_weight`
- Phase 3 donors are protected

## Result vs Phase3-Rate Baseline
- annualized return: `12.1636%` -> `12.2091%`
- Sharpe: `0.8839` -> `0.8881`
- Sortino: `1.0481` -> `1.0521`
- max drawdown: `-23.9949%` -> `-23.9980%`
- Calmar: `0.5069` -> `0.5088`
- profit factor: `4.4139` -> `4.5340`
- win rate: `0.6246` -> `0.6141`
- closed trades: `562` -> `526`

## Critical Finding
This layer did **not** activate in the 20-year production run.

Diagnostics:
- overlay active ratio: `0.00%`
- overlay average active count: `0.0000`
- overlay weight mean: `0.0000`
- overlay routing enabled flag: `True`

Interpretation:
- The improvement in headline metrics is real.
- But it is not coming from successful routing.
- It is coming from the overlay sleeve being effectively absent under the routing constraints.
- In practical terms, this variant currently behaves like `phase3rate` with the additive overlay removed.

## Implication
This is not yet evidence that capital-routing overlay logic adds alpha.
It is evidence that the existing additive overlay may be dilutive.

That distinction matters:
- keep as a useful falsification result
- do not describe it as a successful routing enhancement
- if promoted, promote it as an `overlay disabled / overlay suppressed` profile, not as a validated routing architecture

## Episode Validation
- improved buckets: `3` of `5`
- improved episodes: `transition_2014_2016, shock_2020, all_other_periods`
- `crisis_2007_2009`: delta return `-0.003503`, delta PnL `-881.63`
- `transition_2014_2016`: delta return `0.010891`, delta PnL `5885.93`
- `shock_2020`: delta return `0.000312`, delta PnL `3965.46`
- `shock_2022`: delta return `-0.000002`, delta PnL `3026.69`
- `all_other_periods`: delta return `0.080431`, delta PnL `20107.66`

## Decision
- Keep the result as an informative comparison artifact.
- Do not treat the routing mechanism itself as validated.
- The economically correct next inference is: test an explicit `overlay off` control and compare it directly to `phase3rate`.
- Only if a new routing design actually produces non-zero overlay activity should it be evaluated as a routing enhancement.
