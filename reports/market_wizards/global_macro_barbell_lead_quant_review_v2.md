# Global Macro Barbell Strategy Review v2

## Audience

Lead Quant review note.

Scope:
- architecture evolution
- implemented changes and empirical evaluation
- exact description of the current best research baseline
- what worked, what did not, and why
- prioritized recommendations to increase CAGR further

## Executive Summary

The central conclusion is now clear.

The original single-engine macro allocator was the wrong economic abstraction. The strategy's edge does not live in continuous macro deployment. It lives in selective monetization of macro dislocations, with a smaller structural carry sleeve filling the flat periods.

The changes that mattered were architectural and payoff-shaping changes, not threshold-tuning.

Current ranking of tested variants over `2005-01-01` to `2025-01-01`:

| Variant | CAGR | Sharpe | Max DD | Profit Factor | Status |
|---|---:|---:|---:|---:|---|
| Adjusted single-engine macro | 1.2798% | 0.2723 | -12.6228% | 1.4200 | reject |
| Barbell base | 8.6992% | 0.6837 | -21.0007% | 2.0719 | keep |
| Step 1 feedback | 8.8932% | 0.6902 | -21.0007% | 2.0615 | keep |
| Step 2 transmission overlay | 9.4293% | 0.7175 | -21.0001% | 2.2257 | keep |
| Step 3 velocity standalone | 9.1248% | 0.6941 | -20.9980% | 2.1315 | do not keep |
| Step 4 phase architecture | 11.4420% | 0.8406 | -23.9957% | 4.0650 | keep |
| Step A reallocation | 11.6476% | 0.8471 | -23.9935% | 4.0946 | keep |
| Step B asymmetric timing | 11.5759% | 0.8423 | -23.9903% | 4.0570 | do not keep |
| Step D extreme concentration | 12.1141% | 0.8807 | -23.9935% | 4.3928 | current research baseline |

Current best research baseline:
- `global_macro_barbell_concentration`
- artifact: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_2005_2025_final.json`
- report: `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_2005_2025_report.md`

## Data, Engine, and Fidelity Notes

### Market Data

Used now:
- daily OHLCV public market data via Yahoo-backed download and local cache
- engine is event-driven, bar-by-bar
- all execution is simulated through the event queue, not vectorized post-processing

### Alt Data

Used now:
- macro proxy series with `release_date` and `tradable_from` fields from `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/data/market_wizards/macro_regimes.csv`
- security master from `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/data/market_wizards/security_master.csv`

Important limitation:
- the macro data architecture is point-in-time aware
- the current macro values are still a curated proxy dataset, not a true official first-release macro vintage database

### Instrument Set

Current barbell universe:
- Engine A: `HYG`, `LQD`
- Engine B: `SPY`, `EEM`, `DBC`, `USO`, `UUP`

Interpretation:
- this is a linear ETF proxy book, not a futures book
- pricing fidelity for the instruments themselves is acceptable for this prototype
- economic fidelity is limited by ETF proxy imperfections, especially on commodities

### Execution Assumptions

Current runner assumptions:
- slippage: `2 bps`
- spread: `6 bps`
- volume impact: `8 bps`
- commission: `5 bps`
- SEC fee and exchange fee included
- leverage: `2.5x`
- portfolio gross cap: `2.5x`
- net cap: `1.2x`
- hard stop: `20%`

## Chronology of Implemented Changes

### 0. Adjusted Single-Engine Global Macro

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_2005_2025_adjusted_final_diagnostics.json`

Result:
- CAGR `1.2798%`
- Sharpe `0.2723`
- max drawdown `-12.6228%`

Diagnosis:
- too binary
- too often flat
- when deployment increased, return quality fell
- economically incorrect abstraction for the observed edge

Decision:
- reject as primary path

### 1. Barbell Split

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_2005_2025_final.json`

Change:
- Engine A added as structural credit carry / beta sleeve
- Engine B isolated as selective macro dislocation sleeve

Result:
- CAGR `8.6992%`
- Sharpe `0.6837`
- max drawdown `-21.0007%`

Why it worked:
- separated baseline return generation from episodic dislocation alpha
- stopped forcing the same signal architecture to solve both jobs

Decision:
- decisive architectural improvement
- keep

### 2. Step 1: Inter-Engine Feedback

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_feedback_2005_2025_final.json`

Change:
- Engine A 5-day and 21-day trailing PnL used as a leading indicator for Engine B threshold modulation
- short threshold reduced under carry stress
- long threshold reduced under carry euphoria

Result:
- CAGR `8.8932%`
- Sharpe `0.6902`

Why it worked:
- credit sleeve PnL contained useful leading information
- improved timing without changing the core economic structure

Decision:
- keep

### 3. Step 2: Transmission-Lag Overlay

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_overlay_2005_2025_final.json`

Change:
- leader signal from Engine A 5-day PnL z-score plus `UUP`
- lag overlay on `SPY`, `EEM`, `DBC`, `USO`
- overlay sized relative to existing Engine B positions

Result:
- CAGR `9.4293%`
- Sharpe `0.7175`
- profit factor `2.2257`

Why it worked:
- increased monetization per dislocation
- did not require broader deployment
- exploited sequencing of cross-asset transmission rather than adding a broad new alpha source

Limitation:
- overlay is economically small
- active ratio is only `3.8943%`

Decision:
- keep, but treat as a sparse auxiliary sleeve rather than a major return engine

### 4. Step 3: Macro Velocity Layer

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_velocity_2005_2025_final.json`

Change:
- macro velocity from changes in region z-scores
- attempted use for sizing boosts and early exits

Result:
- CAGR `9.1248%`
- Sharpe `0.6941`

Why it did not work:
- very weak incremental information on the current proxy macro dataset
- mean velocity multiplier near `1.0`
- exit trigger activity too low to matter

Important nuance:
- standalone velocity did not work
- velocity still remains useful inside the phase architecture as a throttle and gating signal

Decision:
- do not keep as a standalone additive alpha layer
- do not optimize it further on the current macro dataset

### 5. Step 4: Phase-Based Stop Architecture

Artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_phase_2005_2025_final.json`

Change:
- Engine B core phases:
  - Phase 1: half-size, `1.5 ATR` stop
  - Phase 2: full size, `2.5 ATR` stop
  - Phase 3: pressed size, `3.5 ATR` stop
- overlay phases:
  - half-size starter
  - only scales once lag starts closing
  - exits on lag failure or stop

Result:
- CAGR `11.4420%`
- Sharpe `0.8406`
- max drawdown `-23.9957%`
- profit factor `4.0650`

Why it worked:
- payoff geometry improved materially
- early losers stayed smaller
- winners were only pressed after confirmation
- the uplift came from better monetization, not higher average gross exposure

Decision:
- major keep

### 6. Step A: Cross-Engine Capital Reallocation During Engine A Shutoff

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_reallocation_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_step_a_episode_validation.md`

Change:
- when Engine A is shut off, its freed carry gross is reallocated to existing Engine B positions in Phase `>= 2`
- no new positions are created
- only confirmed positions are pressed

Result:
- CAGR `11.6476%`
- Sharpe `0.8471`
- max drawdown `-23.9935%`

Why it worked:
- capital routing improved without changing the signal model
- moved risk budget from a disabled sleeve to already-confirmed dislocation positions

Robustness:
- improved `4 of 5` subperiod evaluation buckets by return vs Step 4

Decision:
- keep

### 7. Step B: Asymmetric Crisis-Expression Phase Timing

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_asymmetric_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_asymmetric_2005_2025_report.md`

Change:
- faster Phase 1 -> Phase 2 promotion for crisis-speed expressions:
  - short `SPY`, `EEM`, `DBC`, `USO`
  - long `UUP`
- crisis timing:
  - `5` bars and `0.75 ATR`
- default timing:
  - `10` bars and `1.0 ATR`

Result:
- CAGR `11.5759%`
- Sharpe `0.8423`

Why it did not work:
- earlier size promotion added exposure before enough confirmation had accumulated
- the crisis-speed classification was directionally correct but too coarse
- Step 4's edge still comes more from payoff shaping after confirmation than from faster promotion into size

Decision:
- do not keep as the baseline

### 8. Step D: Extreme-Regime Concentration

Artifacts:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_2005_2025_final.json`
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_episode_validation.md`

Change:
- in extreme regimes, narrow Engine B from top `3` to top `2` expressions
- extreme regime requires:
  - coherence above historical `90th` percentile of prior active-regime coherence
  - at least one existing Engine B position already confirmed in Phase `>= 2`
- Phase 3 size increases from `1.3x` to `1.5x` during these extreme regimes

Result:
- CAGR `12.1141%`
- Sharpe `0.8807`
- Sortino `1.0433`
- max drawdown `-23.9935%`
- profit factor `4.3928`

Why it worked:
- concentrated risk only when both macro coherence and price confirmation were already present
- pressed the best expressions rather than broadening deployment
- exactly matched the empirical finding that monetization per dislocation matters more than time-in-market

Robustness:
- improved `4 of 5` subperiod evaluation buckets by return vs Step A

Decision:
- promote to the new research baseline

## Current Strategy: Exact Functional Description

This section describes the current best research baseline:
- `global_macro_barbell_concentration`

### Rebalance Cadence and Event Loop

The engine is daily and event-driven.

At each daily bar:
1. update OHLCV histories and position state
2. update point-in-time regional macro state
3. update Engine A shutoff / re-entry state
4. refresh core barbell plans weekly, or immediately if Engine A state changed
5. apply transmission overlay daily
6. apply phase architecture daily
7. apply velocity exits daily
8. apply Engine A shutoff reallocation daily
9. generate target-quantity deltas and route them through portfolio/risk checks

Cadence summary:
- core plan refresh: weekly
- overlay and stops: daily
- execution: next bar through the event queue, subject to portfolio constraints

### Engine A: Carry Sleeve

Symbols:
- `HYG`, `LQD`

Target weights:
- default `15%` each

Objective:
- provide baseline carry / credit beta
- provide a leading indicator to Engine B through its realized PnL path

Shutoff triggers:
- `HYG` 20-day drawdown worse than `-3%`
- US `credit` z-score below `-1.5`
- Engine A trailing 60-day PnL below `-2%` of portfolio equity

When shut off:
- Engine A target goes to zero

Re-entry:
- restart at `50%` of target weight
- scale back to full over `4` weekly resize steps

### Engine B: Dislocation Sleeve

Symbols:
- `SPY`, `EEM`, `DBC`, `USO`, `UUP`

Objective:
- stay selective
- monetize coherent macro regime breaks

#### Macro Signal Construction

For each region:
- build z-scores for `growth`, `inflation`, `policy`, `liquidity`, `credit`
- normalization uses an expanding / capped trailing framework:
  - target lookback around `36` observations
  - max lookback `72`
  - minimum observations `24`
  - volatility floor prevents overreaction after quiet periods

Themes:
- `REFLATION`
- `DISINFLATION`
- `STAGFLATION`
- `GOLDILOCKS`
- `MIXED`

Thematic mapping:
- theme determines preferred sign by asset class / instrument
- relative overlays compare US macro state vs Europe / Japan / EM and mainly affect `SPY`, `EEM`, `UUP`

#### Trend Filters

Asset-specific trend filters:
- credit and duration: `SMA50`
- commodities and FX: `SMA100`
- equities: `SMA200`

Trend is used as a multiplier / gate inside the target weight construction rather than as a standalone signal.

#### Engine B Gross Target

Gross target is step-function based on score magnitude:
- below `0.80`: flat
- `0.80` to `<1.20`: `25%` of max Engine B gross
- `1.20` to `<1.80`: `60%` of max Engine B gross
- `>=1.80`: full Engine B gross

Max Engine B gross:
- `2.15x`

#### Inter-Engine Feedback

Engine A trailing PnL modifies Engine B entry sensitivity:
- carry stress lowers the short threshold multiplier to `0.70`
- carry euphoria lowers the long threshold multiplier to `0.80`

This is a timing aid, not a replacement for macro or price confirmation.

### Transmission-Lag Overlay

Overlay symbols:
- `SPY`, `EEM`, `DBC`, `USO`

Leader signal:
- Engine A 5-day PnL z-score
- plus standardized `UUP` move

Logic:
- overlay is only considered when the relevant core Engine B position is already active
- if the fast confirmation signal is ahead of the slow asset, add a small overlay in the same direction
- overlay is capped relative to the existing core position

This layer is sparse by design. It is not the main return engine.

### Phase Architecture

Core phases:
- Phase 1:
  - `0.5x` size
  - `1.5 ATR` stop
- Phase 2:
  - `1.0x` size
  - `2.5 ATR` stop
- Phase 3:
  - baseline `1.3x` size
  - `3.5 ATR` stop

Promotion rules:
- Phase 1 -> Phase 2:
  - approximately `10` bars held
  - profit at least `1 ATR`
- Phase 2 -> Phase 3:
  - approximately `30` bars held
  - profit at least `3 ATR`
  - sufficient coherence
  - velocity not decelerating materially

Why this matters:
- the system starts small
- only proven positions get sized up
- this is the largest contributor to the improvement in profit factor

### Step A Reallocation

When Engine A is shut off:
- freed Engine A gross is treated as available capital
- it is reallocated only to existing Engine B positions with Phase `>= 2`
- allocation is pro-rata to confirmed Engine B core weights
- no new positions are created through this layer

This is a capital-routing improvement, not a signal expansion.

### Step D Extreme-Regime Concentration

Extreme regime condition:
- current active-regime coherence above the historical `90th` percentile
- plus at least one confirmed Phase `>= 2` Engine B position already active

When true:
- Engine B narrows from top `3` to top `2` expressions
- Phase 3 multiplier rises from `1.3x` to `1.5x`

This is the current best-performing addition after Step A.

### Current Baseline Diagnostics

From the current Step D baseline:
- Engine A shutoff ratio: `20.3258%`
- Engine B dormant ratio: `46.6720%`
- Engine A reallocation active ratio: `2.3048%`
- extreme regime active ratio: `2.0664%`
- overlay active ratio: `3.2982%`
- Engine B phase 3 ratio: `3.0797%`

Interpretation:
- the system is still selective
- the return profile is being driven by high-quality pressing of rare states, not constant deployment

## What Worked and Why

### Worked

1. The barbell split
- biggest structural improvement
- aligned the architecture with the actual return profile

2. Inter-engine feedback
- low-complexity, information-rich timing improvement
- used carry sleeve stress as a leading input

3. Transmission-lag overlay
- improved monetization per dislocation
- stayed sparse and selective

4. Phase architecture
- strongest single economic improvement after the barbell split
- materially improved payoff geometry

5. Cross-engine capital reallocation
- sensible capital routing
- improved confirmed dislocation monetization without broadening risk unnecessarily

6. Extreme-regime concentration
- best post-Step-A improvement
- concentrated into the highest-quality expressions only when both macro coherence and price confirmation were already present

## What Did Not Work and Why

### Did not work

1. Continuous deployment in the single-engine framework
- diluted episodic edge
- increased low-quality activity

2. Standalone macro velocity layer
- too weak on the current macro proxy dataset
- not enough incremental information content

3. Broad asymmetric phase timing
- economically plausible but too coarse in practice
- promoted size too early, before enough confirmation had accumulated

## Remaining Risks and Limitations

1. Macro data quality remains the largest research bottleneck.
- architecture is PiT-aware
- values are still proxy macro series, not true first-release macro vintages

2. Commodity proxies are imperfect.
- `USO` and `DBC` are practical but structurally noisy proxies for macro commodities

3. The overlay is still economically small.
- it helps, but it is not yet large enough to be a primary CAGR driver

4. The strategy is still selective by design.
- that is economically correct
- but it means CAGR depends on monetizing a small number of high-conviction windows extremely well

## Recommendations to Increase CAGR Further

These are ordered by expected value and practical tractability.

### 1. Rework Step C from additive overlay into capital routing

Current issue:
- the transmission overlay is useful but small
- its active ratio is low and its capital footprint is tiny

Recommendation:
- convert the overlay from “add a small extra sleeve” into “route capital from the weakest active Engine B expression into the lagging one when transmission signal is strong”
- that preserves total gross while making the overlay economically meaningful

Why this is the best next step:
- it attacks monetization per dislocation directly
- it does not require broader deployment
- it is consistent with the data already showing that concentration helps

### 2. Add per-episode, per-instrument attribution for Step D

Need:
- decompose which instruments drive the Step D uplift in:
  - `2007-2009`
  - `2014-2016`
  - `2020`
  - `2022`
  - all other periods

Why:
- if the Step D gain comes mostly from one instrument or one episode, the apparent robustness is overstated
- if the uplift is spread across multiple episodes and symbols, Step D should be treated as structurally sound

### 3. Improve capital routing during Engine A shutoff

Current Step A improvement is positive but still modest.

Recommendation:
- explicitly route shutoff capital only into the strongest live Engine B expressions rather than pro-rata across all eligible Phase `>=2` positions
- use score rank, phase state, and available exposure room together
- this is effectively a more discriminating version of Step A

Why this likely helps:
- current reallocation active ratio is only `2.3048%`
- the mechanism is right, but the capital routing is still blunt

### 4. Upgrade the macro dataset before revisiting velocity or deeper timing layers

Do not optimize velocity further on the current proxy data.

Recommendation:
- replace the current proxy macro series with higher-quality public first-release macro vintages where possible
- keep `release_date` and `tradable_from` discipline
- then re-test:
  - velocity
  - policy reversal filters
  - more refined crisis activation logic

Why:
- timing layers are only as good as the macro measurement quality

### 5. Improve the commodity sleeve proxies

Recommendation:
- test alternative public proxies for commodity expression quality
- especially validate whether `USO` is helping because of true macro sensitivity or because of incidental ETF behavior

Why:
- Step D suggests concentration works
- concentration should be applied to the cleanest expressions, not just the available ones

### 6. Add a stricter dynamic concentration schedule

Recommendation:
- keep the current top-2 extreme-regime logic as the baseline
- test a second tier:
  - top-1 concentration only when coherence is even higher and one Phase-3 winner already exists
- do this only after episode attribution confirms that concentrated winners are not too single-name dependent

Why:
- the current evidence says concentration is rewarded
- but pushing concentration further without attribution is premature

### 7. Do not spend near-term research budget on these

Avoid for now:
- further standalone velocity calibration
- more instruments in Engine B
- larger Engine A weight
- broad relative-value expansion as the primary next task
- broad threshold loosening to reduce dormancy

Reason:
- these push toward broader deployment
- the tested evidence consistently says broader deployment is not the main path to higher CAGR here

## Recommended Research Baseline and Next Experiments

### Baseline to Carry Forward

Use:
- `global_macro_barbell_concentration`

Reference artifact:
- `/Users/vincentpechstein/Downloads/Pixel-lab/Backtesting/reports/market_wizards/global_macro_barbell_concentration_2005_2025_final.json`

### Immediate Next Experiment Order

1. Step C as capital routing, not additive overlay
2. per-episode / per-instrument attribution under Step D
3. sharper Step A routing into top-ranked confirmed positions
4. macro dataset upgrade
5. only then revisit timing refinements like policy-reversal or velocity variants

## Bottom Line

The research now has a coherent direction.

The winning sequence was not “deploy more often.” It was:
- separate carry from dislocation alpha
- use cross-engine information flow
- shape payoff geometry
- route capital into already-confirmed winners
- concentrate only in truly extreme regimes

That path took the strategy from:
- `1.28%` CAGR in the adjusted single-engine allocator

to:
- `12.11%` CAGR in the current Step D barbell concentration baseline

without materially worsening max drawdown beyond the Step 4/Step A regime.

The next CAGR gains should come from smarter capital routing and cleaner macro measurement, not from adding more activity.
