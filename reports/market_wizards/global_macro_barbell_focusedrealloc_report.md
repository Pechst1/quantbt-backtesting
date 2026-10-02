# Global Macro Barbell: Focused Reallocation Layer

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_focusedrealloc_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_focusedrealloc_diagnostics.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_focusedrealloc_attribution.json`

## Change
Implemented the more aggressive Step A + Step D merge:
- when Engine A is shut off and Step D extreme concentration is active,
- route freed carry gross to the single strongest confirmed extreme-regime expression,
- otherwise fall back to the existing pro-rata Step A logic.

## Validation
- `45 passed` on `pytest -q`

## 20Y Result vs Phase3-Rate Baseline
The result is functionally unchanged.

- annualized return: `12.1636%` -> `12.1636%`
- Sharpe: `0.8839` -> `0.8839`
- Sortino: `1.0481` -> `1.0481`
- max drawdown: `-23.9949%` -> `-23.9949%`
- profit factor: `4.4139` -> `4.4139`
- closed trades: `562` -> `562`

Diagnostics are also unchanged except for the enablement flag:
- `engine_a_focused_reallocation_enabled = True`
- `engine_a_reallocation_active_ratio = 2.2849%`
- `engine_b_extreme_active_ratio = 2.0664%`

## Interpretation
This layer is implemented correctly but inert on the current 20-year sample.

The likely reason is structural overlap:
- closed trades with both `reallocation_days > 0` and `extreme_regime_days > 0`: `4`
- net PnL of that overlap set: `73933.21`

That means the opportunity set where this new rule could differ from the existing pro-rata Step A logic is too small in the current sample to change portfolio-level behavior.

## Decision
- Keep the implementation in the codebase as a valid experimental mode.
- Do not promote it as a new research baseline.
- The stronger conclusion is that the current bottleneck is not the shape of Step A routing inside extreme-regime overlap. The overlap itself is sparse.

## Best next move from here
If the objective remains higher CAGR, the next higher-value path is not more Step A routing complexity. It is one of:
1. upgrade the macro dataset so extreme-regime classification becomes richer and activates on more economically relevant windows
2. introduce explicit concentration/ranking inside the existing reallocation overlap only after proving there are enough overlap events to matter
3. test an `overlay off` control formally, since the prior routing experiment showed the additive overlay was likely dilutive
