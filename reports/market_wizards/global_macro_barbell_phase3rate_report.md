# Global Macro Barbell: Phase 2 to 3 Profit-Rate Layer

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase3rate_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase3rate_diagnostics.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase3rate_attribution.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase3rate_episode_validation.json`

## Change
Replaced the hard Phase 2 -> Phase 3 time gate with an experimental profit-rate promotion rule.

Old Phase 3 promotion:
- `bars_held >= 30`
- `profit_atr >= 3.0`
- coherence and velocity filters

New profit-rate promotion:
- `bars_held >= 12`
- `profit_atr >= 3.0`
- `profit_atr / bars_held >= 0.12`
- coherence and velocity filters

The objective was not broader deployment. The objective was earlier pressing of trades that are already proving themselves quickly.

## Result vs Step D Baseline
- annualized return: `12.1141%` -> `12.1636%`
- Sharpe: `0.8807` -> `0.8839`
- Sortino: `1.0433` -> `1.0481`
- max drawdown: `-23.9935%` -> `-23.9949%`
- Calmar: `0.5049` -> `0.5069`
- profit factor: `4.3928` -> `4.4139`
- closed trades: `563` -> `562`

Headline interpretation:
- The layer improved return and risk-adjusted metrics slightly.
- The improvement is real but small.
- Drawdown stayed effectively unchanged.

## Diagnostics
- Engine B phase 3 active ratio: `3.08%` -> `1.31%`
- Engine A reallocation active ratio: `2.30%` -> `2.28%`
- Engine B extreme active ratio: `2.07%` -> `2.07%`
- Engine B average gross: `34.39%` -> `34.36%`

Interpretation:
- The gain did not come from more average gross exposure.
- The gain came from slightly better monetization of qualified trades.
- The lower daily phase-3 ratio with better results implies the rule is selecting fewer phase-3 days but better phase-3 trades.

## Attribution Shift
Phase 3 participation:
- baseline: `35` trades, net PnL `775115`, PF `42.07`
- phase3-rate: `37` trades, net PnL `915889`, PF `48.21`

Phase 2+ participation:
- baseline net PnL: `1655994`
- phase3-rate net PnL: `1676507`

Reallocation participation:
- baseline net PnL: `1178757`
- phase3-rate net PnL: `1199002`

Interpretation:
- The rule improves the quality of the highest-conviction bucket.
- The delta is consistent with the thesis from the attribution appendix: the next gains come from monetizing confirmed states better, not from being active more often.

## Episode Validation
See `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase3rate_episode_validation.json`.

## Episode Validation Detail
- improved buckets: `3` of `5`
- improved episodes: `crisis_2007_2009, shock_2022, all_other_periods`
- `crisis_2007_2009`: delta return `0.000100`, delta PnL `25.25`
- `transition_2014_2016`: delta return `-0.000006`, delta PnL `11.63`
- `shock_2020`: delta return `-0.000012`, delta PnL `13.02`
- `shock_2022`: delta return `0.014118`, delta PnL `25388.10`
- `all_other_periods`: delta return `0.087004`, delta PnL `21751.01`

Decision:
- Keep as an experimental improvement over Step D.
- The gain is broad enough to justify retention, but too small to promote as a major architectural step on its own.
