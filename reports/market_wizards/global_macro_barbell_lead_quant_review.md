# Global Macro Barbell Strategy Review

## Audience

Lead Quant review note.  
Scope: architecture, implemented changes, empirical evaluation, current operating logic, and next research directions to increase CAGR.

## Executive Summary

The key architectural conclusion is now clear:

- the original single-engine macro allocator was the wrong abstraction
- the strategy's edge lives in regime dislocations, not continuous macro deployment
- forcing higher deployment without restructuring diluted the edge
- the two-engine barbell architecture was the correct redesign

What worked best:

- splitting the strategy into an always-on carry sleeve and a selective dislocation sleeve
- using Engine A PnL as a leading confirmation input for Engine B
- adding a sparse transmission-lag overlay
- reshaping the payoff function via phase-based sizing and stop architecture

What did not work:

- increasing deployment through smoother continuous sizing in the single-engine design
- using macro velocity as a standalone additive alpha layer

Current status:

- conservative default production path: Step 2 barbell with feedback + transmission overlay
- strongest research variant: Step 4 barbell with phase-based stop architecture

## Chronology Of Implemented Changes

### 0. Adjusted Single-Engine Global Macro

This was the reworked single-book macro ETF allocator before the barbell split.

20-year result:

| Metric | Value |
|---|---:|
| Annualized return | 1.2798% |
| Sharpe | 0.2723 |
| Max drawdown | -12.6228% |
| Profit factor | 1.4200 |
| Avg gross exposure | 21.1575% |
| Monthly zeroish ratio | 20.50% |

Diagnosis:

- underdeployed for long periods
- structurally too binary
- episodic edge was being smoothed into a weak continuous allocator

### 1. Barbell Split

New architecture:

- Engine A: credit carry / beta sleeve
  - `HYG`, `LQD`
- Engine B: macro dislocation sleeve
  - `SPY`, `EEM`, `DBC`, `USO`, `UUP`

20-year result:

| Metric | Value |
|---|---:|
| Annualized return | 8.6992% |
| Sharpe | 0.6837 |
| Max drawdown | -21.0007% |
| Profit factor | 2.0719 |

Attribution:

- Engine A net PnL: `68,969`
- Engine B net PnL: `1,044,530`

Conclusion:

- the architectural split solved the central economic problem
- the carry sleeve filled flat years
- the dislocation sleeve retained episodic convexity

### 2. Step 1: Inter-Engine Feedback

Added:

- Engine A 5-day and 21-day trailing PnL as a leading indicator for Engine B
- short threshold reduced when carry sleeve shows stress
- long threshold reduced when carry sleeve shows euphoria

20-year result:

| Metric | Value |
|---|---:|
| Annualized return | 8.8932% |
| Sharpe | 0.6902 |
| Max drawdown | -21.0007% |

Delta vs barbell base:

- CAGR: `+0.1940%`
- Sharpe: `+0.0065`

Conclusion:

- keep
- low-risk, positive-information change

### 3. Step 2: Transmission-Lag Overlay

Added:

- leader signal from Engine A 5-day PnL z-score plus `UUP`
- lag overlay on `SPY`, `EEM`, `DBC`, `USO`
- symbol-specific leader thresholds from historical quantiles
- asset-specific holding horizons
- overlay sized relative to existing Engine B position

20-year result:

| Metric | Value |
|---|---:|
| Annualized return | 9.4293% |
| Sharpe | 0.7175 |
| Max drawdown | -21.0001% |
| Profit factor | 2.2257 |

Delta vs Step 1:

- CAGR: `+0.5361%`
- Sharpe: `+0.0273`
- Engine B net PnL: `+141,509`

Overlay diagnostics:

- active ratio: `3.8943%`
- average active overlay weight: `0.4740%`

Conclusion:

- strong keep
- sparse, selective, additive
- improved monetization per dislocation without turning into another always-on risk source

### 4. Step 3: Macro Velocity Layer

Added:

- monthly macro velocity from z-score changes
- Engine B sizing multiplier from velocity
- early exits on velocity deceleration
- overlay exits on velocity deceleration

Important implementation note:

- the velocity layer is in the codebase
- it is disabled by default

20-year result with velocity explicitly enabled:

| Metric | Value |
|---|---:|
| Annualized return | 9.1248% |
| Sharpe | 0.6941 |
| Max drawdown | -20.9980% |

Delta vs Step 2:

- CAGR: `-0.3045%`
- Sharpe: `-0.0234`

Diagnostics:

- mean velocity multiplier: `1.0039`
- velocity exit ratio: effectively near zero

Conclusion:

- correct implementation
- weak economic value on this dataset
- do not enable by default

### 5. Step 4: Phase-Based Stop Architecture

Added:

- core position phases
  - Phase 1: half-size, tight stop
  - Phase 2: full size, wider stop
  - Phase 3: pressed size, widest stop
- overlay phases
  - half-size on entry
  - scales only after lag closure begins
  - exits on lag failure or phase stop
- velocity/coherence used as phase advancement filter, not as primary alpha

20-year result:

| Metric | Value |
|---|---:|
| Annualized return | 11.4420% |
| Sharpe | 0.8406 |
| Max drawdown | -23.9957% |
| Profit factor | 4.0650 |

Delta vs Step 2:

- CAGR: `+2.0128%`
- Sharpe: `+0.1231`
- Max drawdown: `-2.9956%`
- Engine B net PnL: `+594,565`

Key diagnostic point:

- Engine B average gross exposure fell versus Step 2
- return improved anyway

This means the improvement came from better payoff geometry, not just more capital deployment.

## What Worked And What Did Not

### What Worked

#### A. Barbell Separation

This was the highest-impact change.

Why it worked:

- it matched the empirical return stream
- it stopped trying to force dislocation alpha into a continuous allocator
- it gave the portfolio a baseline carry sleeve while preserving a separate regime-break engine

#### B. Inter-Engine Feedback

Why it worked:

- credit/carry stress is a real market signal, not just a portfolio accounting artifact
- it moved Engine B slightly earlier without loosening the base model indiscriminately

#### C. Transmission-Lag Overlay

Why it worked:

- it increased PnL per dislocation rather than time in market
- it used cross-asset sequencing, which is a plausible macro transmission mechanism
- it remained sparse and capacity-aware

#### D. Phase-Based Stop Architecture

Why it worked:

- it changed the payoff ratio, not just exposure
- it made losers smaller at entry
- it allowed larger participation only after trades proved themselves

This is the first extension that clearly improved CAGR and Sharpe simultaneously in a meaningful way.

### What Did Not Work

#### A. The Single-Engine “Deploy More Capital” Path

Why it failed:

- it mistook inactivity for inefficiency
- extra deployment was funded by lower-quality marginal trades

#### B. Standalone Macro Velocity

Why it failed:

- the monthly macro proxy series is too sparse and too weak for velocity to add much independent alpha
- the best use of velocity is as a throttle on position evolution, not as a separate signal source

#### C. Threshold-Loosening / Continuous Exposure Pressure

Why it failed:

- it increased trade count faster than it increased information content
- the strategy's edge is not broad enough to support indiscriminate deployment

## How The Strategy Functions Now

There are now two materially different modes:

- default conservative mode: Step 2 barbell
- research mode: Step 4 barbell with phase architecture

### Data And Point-In-Time Discipline

Market data:

- public OHLCV from Yahoo Finance
- cached locally in Parquet
- adjusted prices supported

Alternative data:

- point-in-time security master
- point-in-time macro snapshots with:
  - `as_of`
  - `release_date`
  - `tradable_from`

Important caveat:

- the macro architecture is point-in-time correct
- but the macro file is still a curated proxy series, not a true official first-release macro database

### Portfolio Structure

#### Engine A: Carry Sleeve

Instruments:

- `HYG`
- `LQD`

Intent:

- baseline carry / credit beta
- secondary role as a leading stress/euphoria indicator for Engine B

Rules:

- fixed target weights, typically 15% each
- shut off if:
  - `HYG` 20-day drawdown breaches threshold
  - US credit macro z-score breaches threshold
  - trailing carry sleeve PnL breaches its drawdown budget
- re-enter gradually over 4 weeks after shutoff clears

#### Engine B: Dislocation Sleeve

Instruments:

- `SPY`
- `EEM`
- `DBC`
- `USO`
- `UUP`

Intent:

- deploy only in coherent macro breaks
- concentrate into the cleanest expressions

### Engine B Signal Construction

For each symbol:

1. pull the latest tradable regional macro snapshot
2. z-score the five macro factors against trailing history:
   - growth
   - inflation
   - policy
   - liquidity
   - credit
3. map region into a macro theme:
   - `REFLATION`
   - `DISINFLATION`
   - `STAGFLATION`
   - `GOLDILOCKS`
   - `MIXED`
4. compute a symbol-level exposure score from:
   - asset-class/sector exposure template
   - optional adaptive weights when enabled
5. combine with:
   - theme-expression sign
   - relative overlay where relevant
   - coherence adjustment

The coherence adjustment dampens noisy factor disagreement and increases conviction when factors align.

### Engine B Position Selection

- rank the dislocation sleeve by absolute score
- keep only top `N` candidates
- gross target is stepwise by conviction
- trend confirmation is still enforced through asset-specific trend logic
- portfolio caps remain in force

### Step 1 Feedback Layer

Engine A trailing PnL modifies Engine B thresholds:

- carry stress lowers short activation threshold
- carry euphoria lowers long activation threshold

This is a timing refinement, not a separate alpha engine.

### Step 2 Overlay

The transmission overlay activates only when:

- Engine B already has a core directional position
- leader signal is strong enough
- the slower asset has not caught up yet

Leader signal:

- Engine A 5-day PnL z-score
- plus `UUP` standardized move

Slow assets:

- `SPY`
- `EEM`
- `DBC`
- `USO`

The overlay is small and capped relative to core exposure.

### Step 3 Velocity Layer

Still available, but disabled by default.

When enabled:

- macro velocity is computed from monthly z-score changes
- it can adjust sizing and trigger exits

Current conclusion:

- not robust enough to justify default-on deployment

### Step 4 Phase-Based Stop Layer

Research mode only.

When enabled:

- Engine B core starts in Phase 1:
  - smaller size
  - tight stop
- if the trade works:
  - it advances to fuller size
  - stop widens
- only a small subset reaches the pressed Phase 3

Overlay positions follow similar logic:

- small entry
- scale only after lag closure begins
- exit if lag fails

This is the main source of the recent CAGR improvement.

## Current Recommendation On What To Treat As “The Strategy”

For production/conservative evaluation:

- treat Step 2 as the current baseline strategy

For research/high-potential evaluation:

- treat Step 4 as the best current candidate

This separation matters because Step 4 improves the economics, but with a higher drawdown envelope and more implementation complexity.

## Recommendations To Further Increase CAGR

These are ordered by expected value, not by coding convenience.

### 1. Validate Step 4 Properly Before Adding More Logic

Step 4 is the best result so far, but it may be harvesting a small number of high-payoff episodes.

Required before promotion:

- walk-forward validation
- leave-one-crisis-out validation
- subperiod analysis
- regime-conditioned attribution

If Step 4 is not robust across dislocations, further stacking features on top of it will be misleading.

### 2. Upgrade The Macro Dataset

This is the highest-probability true alpha improvement.

Current problem:

- the macro architecture is good
- the macro values are still proxy-quality

Likely improvement path:

- replace the current macro proxy file with first-release public data wherever possible
- use ALFRED/FRED vintages for US macro series
- add cleaner regional macro release handling for Europe, Japan, and EM

Why this matters:

- Step 3 likely underperformed partly because velocity on proxy data is weak
- higher-quality macro vintages should improve both regime detection and phase advancement

### 3. Replace Weak ETF Proxies With Cleaner Macro Expressions

Current weakest structural issue:

- `USO` and `DBC` are ETF proxies with product-specific contamination
- this can distort both signal quality and stop behavior

Research path:

- where possible, switch to continuous futures proxies or cleaner total-return proxies
- at minimum, benchmark current ETF results against a continuous-futures surrogate

This should increase capital efficiency and reduce product noise.

### 4. Make Engine B Concentration Dynamic

Current Engine B uses a fixed top-`N`.

Better idea:

- moderate dislocation: top 3
- strong coherent dislocation: top 2
- extreme coherent dislocation: top 1 plus higher allowed phase-3 allocation

Rationale:

- the strategy's edge is not broad
- high-conviction regimes should be expressed more narrowly, not more broadly

### 5. Add Relative-Value Macro Expressions Inside Engine B

Current Engine B remains mostly directional.

High-potential extension:

- explicit `SPY` vs `EEM`
- `SPY` vs `EFA`
- `UUP` vs risk assets

Use case:

- mixed or ambiguous macro regimes
- high differential, low absolute conviction environments

This can raise CAGR without increasing market beta proportionally.

### 6. Add Conditional Gross Scaling Only For Phase 3

Step 4 improved returns while lowering average Engine B gross.

That creates room for a disciplined next test:

- do not raise gross exposure generally
- only allow a modest gross increase when:
  - coherence is high
  - phase is 3
  - overlay is active or transmission is confirmed

This is a much cleaner leverage experiment than raising baseline gross everywhere.

### 7. Improve Engine A Rather Than Merely Enlarging It

Do not simply make Engine A bigger.

Instead:

- improve its information content and carry efficiency
- consider credit-spread or duration-hedged credit expressions
- keep its drawdown budget explicit and separate

Goal:

- protect the portfolio's baseline return floor
- preserve risk budget for Engine B in crises

### 8. Revisit Step 3 Only After Better Data

Step 3 should not be discarded conceptually.

But it should not be optimized further on the current proxy dataset.

Return to it only after:

- better macro vintages
- cleaner regional release data
- or a richer velocity construction that includes market-based macro proxies

## Bottom Line

The project is now in a materially better place than at the single-engine stage.

What is now firmly established:

- the edge is real
- the barbell architecture is correct
- transmission lag is a valid second-layer monetization mechanism
- phase-based payoff shaping is the strongest improvement tested

What remains unresolved:

- whether the Step 4 uplift is robust enough for default deployment
- how much of the remaining CAGR ceiling is blocked by macro data quality rather than strategy logic

Practical recommendation for next decision:

- treat Step 2 as the operational baseline
- treat Step 4 as the primary research candidate
- spend the next research cycle on robustness validation and data quality, not on stacking more heuristics
