# Global Macro Barbell: Exact Attribution Appendix

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_attribution.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_episode_validation.json`

## Scope
This appendix closes the attribution gap identified after the Step D concentration run. The objective was to move from inferred behavior to explicit trade-level evidence.

Implemented instrumentation:
- `ClosedTrade` now stores `entry_timestamp`, `exit_timestamp`, `entry_price`, `exit_price`, `direction`, and structured `metadata`.
- `Portfolio` now keeps open-trade context and merges strategy metadata across adds, daily state updates, and exits.
- The barbell strategy now pushes daily plan state into the portfolio for open positions, including `engine`, `theme`, `region`, `score`, `coherence`, `velocity_signal`, `core_phase`, `overlay_phase`, `reallocation_target_weight`, and `extreme_regime`.
- Daily observation counters now persist into closed trades: `phase_1_days`, `phase_2_days`, `phase_3_days`, `overlay_phase_*_days`, `extreme_regime_days`, and `reallocation_days`.
- A strategy-path bug was fixed: `_apply_transmission_overlay()` was clearing `extreme_regime` on overlay symbols. Earlier attribution therefore undercounted extreme-regime participation.

Validation status:
- `38 passed` on `pytest -q`

## Baseline Integrity
The exact-attribution instrumentation did not change the Step D baseline economics:
- annualized return: `12.1141%`
- Sharpe: `0.8807`
- Sortino: `1.0433`
- max drawdown: `-23.9935%`
- profit factor: `4.3928`
- closed trades: `563`
- Engine A average gross: `20.03%`
- Engine B average gross: `34.39%`

## Exact Attribution Findings
### Engine split
- Engine A net PnL: `85127`
- Engine B net PnL: `2099931`
- Engine B share of realized trade PnL: `96.1%`

Interpretation:
- Engine B remains the economic center of the strategy.
- Future CAGR work should continue to focus on monetization of confirmed Engine B positions rather than making Engine A busier.

### Phase quality
- Phase 2+ participated trades: `140` of `563`
- Phase 2+ net PnL: `1655994` (`75.8%` of total)
- Phase 2+ win rate: `77.1%`
- Phase 2+ profit factor: `12.51`
- Phase 2+ average max target weight: `0.73`

- Phase 3 participated trades: `35` of `563`
- Phase 3 net PnL: `775115` (`35.5%` of total)
- Phase 3 win rate: `85.7%`
- Phase 3 profit factor: `42.07`
- Phase 3 average max target weight: `1.10`
- Phase 3 average holding period: `188.9` days

Interpretation:
- This supports the thesis that the next CAGR gains likely sit in better monetization of confirmed positions.
- It does not support the stronger claim that "all the money is Phase 3." Phase 3 is only `35.5%` of realized PnL today. Phase 1 and Phase 2 still contribute materially because they have far more trades.
- The right read is: Phase 3 has the best payoff geometry, not that the rest of the stack is economically irrelevant.

### Step A reallocation quality
- Reallocation-participated trades: `80`
- Reallocation net PnL: `1178757` (`53.9%` of total)
- Reallocation win rate: `76.2%`
- Reallocation profit factor: `12.34`

Interpretation:
- The shutoff-routing logic is not cosmetic. It touches relatively few trades, but those trades are unusually productive.
- This supports making Step A routing sharper rather than broader.

### Extreme-regime quality
- Extreme-regime participated trades: `24`
- Extreme-regime net PnL: `270861` (`12.4%` of total)
- Extreme-regime win rate: `87.5%`
- Extreme-regime profit factor: `321.31`

Interpretation:
- The extreme-regime slice is high quality, but it is not the dominant carrier of total PnL.
- On current data, extreme-regime participation appears mostly in `transition_2014_2016` and a smaller subset of `crisis_2007_2009`, not in `shock_2020` or `shock_2022`.
- Future changes should therefore not assume that all remaining edge is exclusively trapped in crash-speed windows.

### Best symbols
- `USO`: net PnL `771926`, profit factor `12.42`, closed trades `50`
- `DBC`: net PnL `556323`, profit factor `4.33`, closed trades `101`
- `EEM`: net PnL `517214`, profit factor `4.39`, closed trades `150`
- `SPY`: net PnL `254468`, profit factor `2.42`, closed trades `156`
- `HYG`: net PnL `47800`, profit factor `2.24`, closed trades `53`
- `LQD`: net PnL `37327`, profit factor `1.97`, closed trades `53`

Interpretation:
- The engine is still being carried by a small expression set: `USO`, `DBC`, `EEM`, and `SPY`.
- This supports staying concentrated and continuing to treat additional Engine B instruments as a low priority.

## Episode-Level Evidence
Step D versus Step A subperiod validation:
- `crisis_2007_2009`: Step A return `1.0598`, Step D return `1.0598`, delta return `0.0000`, delta PnL `0`
- `transition_2014_2016`: Step A return `0.3594`, Step D return `0.4775`, delta return `0.1181`, delta PnL `68784`
- `shock_2020`: Step A return `0.6302`, Step D return `0.6302`, delta return `0.0001`, delta PnL `49618`
- `shock_2022`: Step A return `0.2316`, Step D return `0.2316`, delta return `0.0000`, delta PnL `34868`
- `all_other_periods`: Step A return `8.0255`, Step D return `8.8090`, delta return `0.7835`, delta PnL `195869`

Interpretation:
- Step D improved `4 of 5` buckets by return.
- The biggest incremental return came from `all_other_periods`, then `transition_2014_2016`.
- The uplift in `shock_2020` and `shock_2022` exists, but it is not the main driver of the research-baseline improvement.
- This weakens the argument for a purely crisis-speed optimization agenda.

Trade-level episode decomposition for the new attribution fields:

Phase 3 participation:
- `all_other_periods`: trades `4`, net PnL `222639`
- `crisis_2007_2009`: trades `11`, net PnL `85184`
- `shock_2022`: trades `16`, net PnL `393360`
- `transition_2014_2016`: trades `4`, net PnL `73931`

Reallocation participation:
- `all_other_periods`: trades `3`, net PnL `70170`
- `crisis_2007_2009`: trades `38`, net PnL `179736`
- `shock_2020`: trades `15`, net PnL `455263`
- `shock_2022`: trades `18`, net PnL `396084`
- `transition_2014_2016`: trades `6`, net PnL `77504`

Extreme-regime participation:
- `crisis_2007_2009`: trades `3`, net PnL `32219`
- `transition_2014_2016`: trades `21`, net PnL `238641`

Interpretation:
- Phase 3 participation is spread across multiple episodes, which is constructive.
- Reallocation is active in all of the key monetization windows, especially `shock_2020`, `shock_2022`, and `crisis_2007_2009`.
- Extreme-regime participation is much narrower. That suggests the current extreme-regime label is a high-conviction filter, not a broad descriptor of every profitable dislocation.

## What This Changes About The Next Decision
### Supported by the exact attribution
- `Phase 2 -> Phase 3` reform is now supported as a serious next candidate.
- The quality gap between Phase 2+/3 and the rest of the stack is real.
- A profit-rate-based promotion rule is more defensible than a raw shorter time gate.
- The overlay should be converted from additive sizing into capital routing.
- Step A routing should become more selective, not broader.

### Not supported by the exact attribution
- A purely crisis-speed explanation for the remaining edge.
- Broad asymmetric phase acceleration as a default layer.
- More instruments or more always-on deployment.

## Recommended Order From Here
1. Keep the exact attribution layer and use it as the new research baseline for all future changes.
2. Implement the `Phase 2 -> Phase 3` reform next, but make it profit-rate-based rather than crisis-speed-based.
3. Rework the overlay into a routing layer instead of an additive sleeve.
4. Merge Step A routing with Step D concentration logic so Engine A shutoff capital flows to the strongest confirmed Step D expression, not pro-rata.
5. Add `EMB` as a sensor for Engine B thresholding if an EM-specific side experiment is desired.
6. Upgrade the macro dataset in parallel. That remains the highest-ceiling infrastructure improvement.

## Bottom Line
The attribution gap is now materially smaller.

The data supports a narrower and more defensible thesis:
- The strategy wins by monetizing confirmed Engine B states.
- Phase 2+ and especially Phase 3 are real quality states.
- Reallocation works and deserves sharper routing.
- Extreme-regime concentration works, but it is not synonymous with crash mode.
- The next CAGR improvement should still come from better capital routing and better promotion into proven trades, not from making the portfolio busier.
