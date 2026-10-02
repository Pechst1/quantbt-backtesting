# Concentrated Macro Dislocation Barbell: Full Technical Handoff

This document is self-contained. It describes the current best strategy, the technical formulas, default parameters, and a compact Python reference implementation that a new research team can reproduce without access to the original codebase.

## 1. Current Best Variant

Strategy name: `Concentrated Macro Dislocation Barbell`

Current best tested configuration:

- Barbell architecture enabled.
- Extreme concentration enabled.
- Phase-3 profit-rate promotion enabled.
- Transmission overlay disabled.
- Liquidation-reversal add-on enabled.
- Crisis-trend and dollar-squeeze sleeves disabled for production baseline.

Backtest window: `2005-01-01` to `2025-01-01`.

| Metric | Value |
|---|---:|
| CAGR | 12.2922% |
| Sharpe | 0.9008 |
| Sortino | 1.0621 |
| Max Drawdown | -23.9965% |
| Calmar | 0.5122 |
| Win Rate | 60.63% |
| Profit Factor | 4.4334 |
| Closed Trades | 574 |

Core interpretation: this is not a continuous global macro allocator. It is a selective macro dislocation detector with a credit carry/sensor sleeve.

## 2. Tradeable Universe

Current ETF proxy universe:

| Symbol | Sleeve | Role |
|---|---|---|
| HYG | Engine A | High-yield credit carry and stress sensor |
| LQD | Engine A | Investment-grade credit carry and stress sensor |
| SPY | Engine B | US equity macro expression |
| EEM | Engine B | Emerging-market equity macro expression |
| DBC | Engine B | Broad commodity proxy |
| USO | Engine B | Oil proxy |
| UUP | Engine B | US dollar proxy |

Optional non-traded sensors:

| Symbol / Series | Role |
|---|---|
| EMB | EM credit stress sensor |
| VIXCLS | Volatility stress sensor |
| T10Y2Y | Yield-curve / policy expectations sensor |
| BAMLEMCBPIOAS | EM credit spread sensor |

Recommended future instrument upgrade:

- Replace `USO` and `DBC` with liquid commodity futures.
- Replace ETFs with futures where possible.
- Add true options/options-on-futures for convex dislocation exposure.

## 3. Macro Factor Model

The model uses five macro factor groups:

1. Growth
2. Inflation
3. Policy
4. Liquidity
5. Credit

For region `r`, factor `f`, and observation time `t`, let the point-in-time raw factor observation be:

```text
x_{r,f,t}
```

The raw observation must be known as of the trading date. Required data discipline:

```text
release_date <= trading_date
tradable_from <= trading_date
```

Use first-release values, not revised values.

## 4. Macro Z-Score Normalization

For each factor independently, compute trailing z-scores using only prior observations.

Let:

```text
P_{f,t} = {x_{f,t-L}, ..., x_{f,t-1}}
```

where:

```text
L <= z_window_max
z_window_max = 72 observations
min_macro_observations = 24
z_window = 36
```

In the implemented baseline, the effective minimum observations are usually 36 once sufficient history exists.

Mean:

```text
mu_{f,t} = mean(P_{f,t})
```

Rolling standard deviation:

```text
sigma_roll_{f,t} = std(P_{f,t})
```

Long-term volatility floor:

```text
sigma_floor_{f,t} = 0.5 * median(rolling_std(P_{f,t}, window=min_obs))
```

Final denominator:

```text
sigma_used_{f,t} = max(sigma_roll_{f,t}, sigma_floor_{f,t}, 1e-6)
```

Z-score:

```text
z_{f,t} = (x_{f,t} - mu_{f,t}) / sigma_used_{f,t}
```

Macro vector:

```text
Z_t = [z_growth, z_inflation, z_policy, z_liquidity, z_credit]
```

## 5. Macro Velocity

Velocity exists in the implementation but is not part of the promoted baseline because proxy macro data was too smooth. It should be retested after first-release ALFRED/FRED data is available.

For factor `f`:

```text
v_{f,t} = z_{f,t} - z_{f,t-1}
```

Magnitude:

```text
velocity_magnitude_t = sqrt(sum_f(v_{f,t}^2))
```

Alignment:

```text
velocity_alignment_t = abs(sum_f(v_{f,t})) / (sum_f(abs(v_{f,t})) + eps)
```

Velocity signal:

```text
velocity_signal_t = velocity_magnitude_t * velocity_alignment_t
```

## 6. Macro Coherence

Coherence measures whether macro factors point in a common direction.

```text
coherence_t = min(abs(mean(Z_t)) / (mean(abs(Z_t)) + eps), 1.0)
```

High coherence means macro shocks are aligned. These are the periods where the strategy historically makes most of its money.

## 7. Theme Classification

Given z-scores:

```text
G = z_growth
I = z_inflation
L = z_liquidity
```

Rules:

```text
if G <= -0.5 and I >= 0.5:
    theme = STAGFLATION
elif G >= 0.5 and I >= 0.25:
    theme = REFLATION
elif G <= -0.5 and I <= 0.25:
    theme = DISINFLATION
elif G >= 0.25 and I <= 0.25 and L >= 0.0:
    theme = GOLDILOCKS
else:
    theme = MIXED
```

## 8. Asset Exposure Templates

The raw macro score is a dot product of factor z-scores and an asset-class exposure template.

Factor order:

```text
[growth, inflation, policy, liquidity, credit]
```

Templates:

| Asset Class | Weights |
|---|---:|
| Equity / equity index | `(0.8, -0.2, -0.5, 0.7, 0.6)` |
| Credit | `(0.7, -0.5, -0.6, 0.5, 0.9)` |
| FX / USD | `(0.5, -0.4, 0.7, -0.3, 0.4)` |
| Commodity / energy | `(0.4, 0.9, -0.2, 0.2, 0.2)` |
| Gold | `(-0.1, 0.9, -0.6, 0.2, -0.1)` |
| Duration bond | `(-0.6, -1.0, -1.0, 0.3, -0.4)` |
| Inflation-linked bond | `(0.2, 0.8, -0.4, 0.3, 0.0)` |

Raw score:

```text
raw_score_{i,t} = clip(dot(W_i, Z_{region(i),t}), -3.0, 3.0)
```

Staleness discipline:

```text
age_days = trading_date - release_date
if age_days > 45:
    score = 0
elif age_days > 30:
    raw_score *= 0.5
```

Quality adjustment:

```text
raw_score *= clip(quality_score, 0.0, 1.0)
```

## 9. Theme Expression Mapping

The strategy only trades symbols that are sensible expressions of the active macro theme.

### Reflation

```text
Long:  equities, commodities, credit, gold
Short: rates, USD
```

### Disinflation

```text
Long:  duration, gold, LQD
Short: equities, commodities, HYG
```

### Stagflation

```text
Long:  gold, inflation-linked assets, commodities
Short: duration, credit, equities
```

### Goldilocks

```text
Long:  equities, credit
Short: duration, USD
```

### Mixed

```text
No directional macro expression by default.
Only relative overlays may activate for SPY, EEM, and UUP.
```

## 10. Relative Overlays

The model can compute relative regional scores, although these are secondary in the current best strategy.

For US vs peer region `p`:

```text
relative_score_{US,p} = 0.5 * (growth_US - growth_p)
                      + 0.3 * (policy_US - policy_p)
                      + 0.2 * (credit_US - credit_p)
```

If either region is MIXED:

```text
multiplier = 0.75
```

Otherwise:

```text
multiplier = 0.50
```

Application:

```text
SPY += multiplier * relative_score
peer_equity -= multiplier * relative_score
UUP += 0.35 * multiplier * relative_score
```

## 11. Engine B Score

For symbol `i`, compute theme sign:

```text
s_theme_i in {-1, 0, +1}
```

If theme is not MIXED and the raw score aligns with the theme:

```text
aligned = s_theme_i * raw_score_i
if aligned > 0:
    score_i = s_theme_i * aligned
```

For SPY, EEM, and UUP, relative overlays can dominate if they have larger absolute score:

```text
if abs(relative_overlay_i) > abs(score_i):
    score_i = relative_overlay_i
```

Final coherence-adjusted score:

```text
engine_b_score_i = clip(score_i * (0.5 + coherence), -3.0, 3.0)
```

## 12. Engine A: Carry And Stress Sensor

Engine A target weights:

```text
HYG = +15%
LQD = +15%
```

Engine A tracks PnL and credit stress.

HYG drawdown over 20 bars:

```text
hyg_dd_t = close_HYG_t / max(close_HYG_{t-19:t}) - 1
```

Engine A trailing PnL ratio:

```text
trailing_pnl_ratio_t = (EngineA_PnL_t - EngineA_PnL_{t-60 calendar days}) / equity_t
```

Shutoff triggers:

```text
if hyg_dd_t <= -0.03:
    shutoff = True
elif z_credit_US <= -1.5:
    shutoff = True
elif trailing_pnl_ratio_t <= -0.02:
    shutoff = True
else:
    shutoff = False
```

Engine A scale:

```text
if shutoff:
    scale_A = 0
elif in_reentry:
    progress = reentry_step / 4
    scale_A = min(0.50 + 0.50 * progress, 1.0)
else:
    scale_A = 1
```

Engine A targets:

```text
target_weight_HYG = 0.15 * scale_A
target_weight_LQD = 0.15 * scale_A
```

## 13. Engine A Feedback Into Engine B

Engine A 5-day and 21-day PnL ratios:

```text
pnl_5d_ratio = (EngineA_PnL_t - EngineA_PnL_{t-5d}) / equity_t
pnl_21d_ratio = (EngineA_PnL_t - EngineA_PnL_{t-21d}) / equity_t
```

Stress:

```text
a_stress = pnl_5d_ratio < 0 and pnl_5d_ratio < 0.4 * pnl_21d_ratio
```

Euphoria:

```text
a_euphoria = pnl_5d_ratio > 0 and pnl_5d_ratio > 0.6 * pnl_21d_ratio
```

Threshold multipliers:

```text
long_threshold_multiplier = 0.80 if a_euphoria else 1.00
short_threshold_multiplier = 0.70 if a_stress else 1.00
```

Optional EMB stress:

```text
emb_5d_z = standardized_price_move(EMB, move_lookback=5, vol_lookback=63)
emb_stress = emb_5d_z <= -1.5
emb_euphoria = emb_5d_z >= 1.5
```

If EMB stress is active, short thresholds for EEM/commodity expressions are reduced further.

## 14. Engine B Activation Thresholds

Default thresholds:

```text
probe_threshold      = 0.80
conviction_threshold = 1.20
max_threshold        = 1.80
engine_b_max_gross   = 2.15
```

Adjusted thresholds:

```text
threshold_i = base_threshold * threshold_multiplier_i
```

Target gross:

```text
if abs(score_i) < probe_threshold:
    gross = 0
elif abs(score_i) < conviction_threshold:
    gross = 0.25 * engine_b_max_gross
elif abs(score_i) < max_threshold:
    gross = 0.60 * engine_b_max_gross
else:
    gross = engine_b_max_gross
```

Only the top ranked names by absolute score are selected.

Default:

```text
top_n = 3
```

Extreme concentration:

```text
if extreme_regime:
    top_n = 2
```

## 15. Extreme Concentration

Coherence history threshold:

```text
extreme_threshold_t = quantile(coherence_history, 0.90)
```

Extreme regime is active when:

```text
extreme_active = coherence_t >= extreme_threshold_t
                 and at least one Engine B position is already confirmed Phase 2+
```

In extreme mode:

```text
selected_top_n = 2
Phase 3 multiplier = 1.50 instead of 1.30
```

## 16. Engine B Weight Allocation

For selected symbols `S`:

```text
score_denominator = sum_{j in S} abs(score_j)
raw_weight_i = gross_target * abs(score_i) / score_denominator
signed_weight_i = sign(score_i) * raw_weight_i
```

Trend filter:

```text
trend_multiplier_i = 1.0 if price confirms trend else 0.50
```

Final pre-phase target:

```text
core_target_weight_i = signed_weight_i * trend_multiplier_i
```

Trend periods:

| Asset Type | Trend Lookback |
|---|---:|
| Credit / rates | 50 bars |
| Commodities / energy / gold / FX | 100 bars |
| Equities | 200 bars |

Trend confirmation:

```text
long confirms  if close >= SMA
short confirms if close <= SMA
```

If trend does not confirm:

```text
trend_multiplier = 0.50
```

## 17. Phase-Based Sizing

Phase system applies to Engine B core positions.

### Phase 1: Probe

```text
weight_multiplier = 0.50
stop = 1.50 * ATR from entry
```

### Phase 2: Confirmed

```text
weight_multiplier = 1.00
stop = 2.50 * ATR from high/low water mark
```

Promotion to Phase 2:

```text
bars_held >= 10
profit_ATR >= 1.0
```

### Phase 3: Press Winner

Default Phase 3:

```text
weight_multiplier = 1.30
stop = 3.50 * ATR from high/low water mark
```

Extreme Phase 3:

```text
weight_multiplier = 1.50
```

Current promoted Phase 3 rule uses profit-rate promotion:

```text
profit_ATR = sign * (close_t - entry_price) / ATR_t
profit_rate_ATR = profit_ATR / max(bars_held, 1)

phase3_ready = bars_held >= 12
               and profit_ATR >= 3.0
               and profit_rate_ATR >= 0.12
               and coherence >= 0.50
```

If velocity layer is enabled, require:

```text
velocity_signal >= velocity_exit_signal
```

But velocity is currently not considered reliable on proxy macro data.

## 18. ATR And Stops

ATR is computed over 20 bars.

True range:

```text
TR_t = max(
    high_t - low_t,
    abs(high_t - close_{t-1}),
    abs(low_t - close_{t-1})
)
```

ATR:

```text
ATR_t = mean(TR_{t-19:t})
```

Long stop:

```text
Phase 1: stop = entry_price - stop_mult * ATR
Phase 2/3: stop = high_water_mark - stop_mult * ATR
```

Short stop:

```text
Phase 1: stop = entry_price + stop_mult * ATR
Phase 2/3: stop = low_water_mark + stop_mult * ATR
```

## 19. Engine A Capital Reallocation

When Engine A is shut off, freed carry capital can be redeployed into confirmed Engine B trades.

Baseline carry gross:

```text
baseline_carry_gross = abs(0.15) + abs(0.15) = 0.30
```

Current carry target gross:

```text
current_carry_target = abs(target_HYG) + abs(target_LQD)
```

Freed gross:

```text
freed_gross = max(baseline_carry_gross - current_carry_target, 0)
```

Eligible Engine B positions:

```text
core_phase >= 2
position exists
position direction agrees with model target
available room under max symbol weight
```

Focused reallocation rule, if enabled:

```text
if extreme_regime and eligible:
    allocate freed_gross to strongest eligible symbol only
else:
    allocate pro-rata by abs(core_target_weight)
```

The promoted baseline uses extreme concentration and Engine A reallocation as part of the research baseline mechanics.

## 20. Liquidation Reversal Add-On

This is the only radical add-on retained in the current best result.

Applies to Engine B symbols except `UUP`:

```text
SPY, EEM, DBC, USO
```

Parameters:

```text
reversal_max_gross = 0.35
reversal_top_n = 2
reversal_drawdown_threshold = -0.12
reversal_confirmation_lookback = 5
reversal_max_symbol_weight = 0.18
reversal_max_hold_bars = 40
reversal_stop_pct = 0.07
```

21-bar drawdown:

```text
dd_21_i = close_i,t / max(close_i,t-20:t) - 1
```

Entry candidate:

```text
dd_21_i <= -0.12
```

Confirmation:

```text
confirmed = close_t > max(close_{t-5:t-1}) or close_t > SMA10_t
```

Impulse score:

```text
impulse_i = abs(dd_21_i) * max(crisis_stress_score, 0.35)
```

Select top two by impulse.

Sizing:

```text
intensity_i = clip(impulse_i / abs(-0.12), 0.25, 1.0)
add_weight_i = min(0.18 * intensity_i, symbol_room, remaining_reversal_gross)
```

Stateful hold condition:

```text
bars_held <= 40
and close_t >= SMA10_t
and close_t >= entry_price * (1 - 0.07)
```

If valid, keep the reversal weight. Otherwise exit the sleeve.

## 21. Portfolio Constraints

The portfolio validates projected exposure before adding positions.

Defaults:

```text
max_gross_total = 2.5
max_net_total = 1.2
max_gross_per_asset_class = 0.70
max_gross_per_region = 0.60
```

Projected exposure:

```text
exposure_i = quantity_i * price_i
gross_total = sum_i abs(exposure_i)
net_total = sum_i exposure_i
```

Constraints:

```text
gross_total / equity <= max_gross_total
abs(net_total) / equity <= max_net_total
gross_by_asset_class / equity <= max_gross_per_asset_class
gross_by_region / equity <= max_gross_per_region
```

If desired target breaches constraints, quantity is binary-searched down to the largest feasible size.

## 22. Target Quantity Formula

Given target weight `w_i`:

```text
desired_qty_i = round((equity * w_i) / price_i)
```

Small rebalance hysteresis:

```text
if same direction and abs(desired_qty - current_qty) < 3:
    keep current_qty

if same direction and abs(delta) / abs(current_qty) < 5%:
    keep current_qty
```

Orders are target-position deltas:

```text
delta_qty = desired_qty - current_qty
```

If `delta_qty > 0`, buy at next open.

If `delta_qty < 0`, sell at next open.

## 23. Daily Event Loop

The strategy is event-driven.

On every market event:

1. Update OHLC history.
2. Sync current position high/low water marks.
3. Build point-in-time macro region state.
4. Update Engine A shutoff/re-entry state.
5. Weekly or state-change refresh: build core barbell plans.
6. Apply overlay layer. Current best baseline has overlay off.
7. Apply phase stop architecture.
8. Apply velocity exits. Velocity currently not promoted.
9. Apply convex proxy. Not promoted.
10. Apply Engine A shutoff reallocation.
11. Apply crisis trend. Not promoted.
12. Apply dollar squeeze. Not promoted.
13. Apply liquidation reversal. Promoted.
14. Convert target weights to target quantities.
15. Apply risk/exposure constraints.
16. Emit target delta orders.
17. Record diagnostics and trade attribution.

## 24. Compact Python Reference Implementation

This is a compact research implementation of the current best logic. It omits execution engine details, slippage, commissions, and data loading. It is intended to make the rules reproducible.

```python
from dataclasses import dataclass
from collections import deque
from math import sqrt
import numpy as np
import pandas as pd

EPS = 1e-9


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def sma(values, n):
    if len(values) < n:
        return None
    return float(np.mean(values[-n:]))


def atr(highs, lows, closes, n=20):
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(len(closes) - n, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return float(np.mean(trs))


def weight_to_qty(equity, price, weight):
    return int(round((equity * weight) / max(price, EPS)))


def drawdown(closes, lookback):
    if len(closes) < lookback:
        return None
    recent = np.array(closes[-lookback:], dtype=float)
    peak = float(np.max(recent))
    return float(recent[-1] / max(peak, EPS) - 1.0)


def standardized_price_move(closes, move_lookback=10, vol_lookback=20):
    if len(closes) < max(move_lookback + 1, vol_lookback + 1):
        return None
    arr = np.array(closes, dtype=float)
    move = arr[-1] / arr[-move_lookback - 1] - 1.0
    rets = np.diff(arr[-(vol_lookback + 1):]) / arr[-(vol_lookback + 1):-1]
    vol = float(np.std(rets, ddof=0))
    if vol <= EPS:
        return None
    return float(move / (vol * sqrt(move_lookback)))


def normalize_macro_history(frame, min_obs=24, z_window=36, z_window_max=72):
    available_prior = max(len(frame) - 1, 1)
    min_obs_eff = max(min_obs, min(z_window, available_prior))
    max_obs = max(min_obs_eff, z_window_max)
    out = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for col in frame.columns:
        values = frame[col].to_numpy(dtype=float)
        z = np.full(len(values), np.nan)
        for idx in range(len(values)):
            prior = values[max(0, idx - max_obs):idx]
            if len(prior) < min_obs_eff:
                continue
            mean = float(np.mean(prior))
            rolling_std = float(np.std(prior, ddof=0))
            std_series = pd.Series(prior).rolling(min_obs_eff, min_periods=min_obs_eff).std(ddof=0).dropna()
            long_med = float(std_series.median()) if not std_series.empty else rolling_std
            std_used = max(rolling_std, 0.5 * long_med, 1e-6)
            z[idx] = (values[idx] - mean) / std_used
        out[col] = z
    return out


def macro_velocity(current_z, previous_z):
    deltas = {k: float(current_z[k] - previous_z[k]) for k in current_z}
    vals = np.array(list(deltas.values()), dtype=float)
    magnitude = float(np.sqrt(np.sum(vals ** 2)))
    alignment = float(abs(np.sum(vals)) / (np.sum(np.abs(vals)) + EPS))
    return deltas, magnitude * alignment, alignment


def coherence(z):
    vals = np.array(list(z.values()), dtype=float)
    return float(min(abs(np.mean(vals)) / (np.mean(np.abs(vals)) + EPS), 1.0))


def classify_theme(z):
    g, i, l = z["growth"], z["inflation"], z["liquidity"]
    if g <= -0.5 and i >= 0.5:
        return "STAGFLATION"
    if g >= 0.5 and i >= 0.25:
        return "REFLATION"
    if g <= -0.5 and i <= 0.25:
        return "DISINFLATION"
    if g >= 0.25 and i <= 0.25 and l >= 0.0:
        return "GOLDILOCKS"
    return "MIXED"


def exposure_template(asset_class):
    asset = asset_class.upper()
    if asset in {"BOND", "DURATION"}:
        return np.array([-0.6, -1.0, -1.0, 0.3, -0.4])
    if asset in {"INFLATION_LINKED_BOND", "TIP"}:
        return np.array([0.2, 0.8, -0.4, 0.3, 0.0])
    if asset == "GOLD":
        return np.array([-0.1, 0.9, -0.6, 0.2, -0.1])
    if asset in {"ENERGY", "COMMODITY", "COMMODITY_FUTURE"}:
        return np.array([0.4, 0.9, -0.2, 0.2, 0.2])
    if asset == "FX":
        return np.array([0.5, -0.4, 0.7, -0.3, 0.4])
    if asset == "CREDIT":
        return np.array([0.7, -0.5, -0.6, 0.5, 0.9])
    return np.array([0.8, -0.2, -0.5, 0.7, 0.6])


def theme_expression_sign(symbol, asset_class, theme):
    sym = symbol.upper()
    equity = {"SPY", "EFA", "EEM", "EWJ"}
    rates = {"TLT", "IEF"}
    credit = {"HYG", "LQD"}
    inflation = {"TIP"}
    gold = {"GLD", "GC=F"}
    commodities = {"USO", "DBC", "CL=F", "HG=F", "ZC=F"}
    fx = {"UUP"}

    if theme == "REFLATION":
        if sym in equity or sym in commodities or sym in credit or sym in gold:
            return 1
        if sym in rates or sym in fx:
            return -1
    if theme == "DISINFLATION":
        if sym in rates or sym in gold or sym == "LQD":
            return 1
        if sym in {"SPY", "EFA", "EEM", "EWJ", "USO", "DBC", "HYG"}:
            return -1
    if theme == "STAGFLATION":
        if sym in gold or sym in inflation or sym in commodities:
            return 1
        if sym in {"TLT", "IEF", "HYG", "SPY", "EFA", "EEM", "EWJ", "LQD"}:
            return -1
    if theme == "GOLDILOCKS":
        if sym in equity or sym in credit:
            return 1
        if sym in {"TLT", "IEF", "UUP"}:
            return -1
    return 0


@dataclass
class Plan:
    symbol: str
    engine: str
    target_weight: float = 0.0
    core_weight: float = 0.0
    reversal_weight: float = 0.0
    score: float = 0.0
    phase: int = 0
    coherence: float = 0.0
    reason: str = ""
    extreme: bool = False


class ConcentratedMacroDislocationBarbell:
    def __init__(self):
        self.carry_symbols = ("HYG", "LQD")
        self.dislocation_symbols = ("SPY", "EEM", "DBC", "USO", "UUP")
        self.all_symbols = self.carry_symbols + self.dislocation_symbols

        self.carry_target = {"HYG": 0.15, "LQD": 0.15}
        self.carry_drawdown_stop = 0.03
        self.carry_credit_z_stop = -1.5
        self.carry_pnl_budget = 0.02
        self.carry_reentry_start = 0.50
        self.carry_reentry_weeks = 4

        self.probe_threshold = 0.80
        self.conviction_threshold = 1.20
        self.max_threshold = 1.80
        self.engine_b_max_gross = 2.15
        self.engine_b_top_n = 3
        self.extreme_top_n = 2

        self.phase1_days = 10
        self.phase1_profit_atr = 1.0
        self.phase2_profit_atr = 3.0
        self.phase3_min_days = 12
        self.phase3_profit_rate = 0.12
        self.phase3_coherence_floor = 0.50
        self.phase_mult = {1: 0.50, 2: 1.00, 3: 1.30}
        self.extreme_phase3_mult = 1.50
        self.stop_mult = {1: 1.50, 2: 2.50, 3: 3.50}

        self.reversal_max_gross = 0.35
        self.reversal_top_n = 2
        self.reversal_dd_threshold = -0.12
        self.reversal_confirm_lookback = 5
        self.reversal_max_symbol_weight = 0.18
        self.reversal_max_hold = 40
        self.reversal_stop_pct = 0.07

        self.max_gross_total = 2.5
        self.max_net_total = 1.2

        self.closes = {s: deque(maxlen=2000) for s in self.all_symbols}
        self.highs = {s: deque(maxlen=2000) for s in self.all_symbols}
        self.lows = {s: deque(maxlen=2000) for s in self.all_symbols}
        self.engine_a_pnl_history = deque(maxlen=2000)
        self.engine_b_state = {}
        self.reversal_state = {}
        self.coherence_history = deque(maxlen=1000)
        self.bar_index = 0
        self.engine_a_shutoff = False
        self.reentry_step = None

    def engine_a_update(self, date, equity, macro_z, positions, prices):
        engine_a_pnl = sum(positions.get(s, 0) * prices[s] for s in self.carry_symbols if s in prices)
        self.engine_a_pnl_history.append((date, engine_a_pnl))

        hyg_dd = drawdown(self.closes["HYG"], 20) or 0.0
        credit_z = macro_z.get("US", {}).get("credit", 0.0)
        pnl_60d = self.trailing_pnl_ratio(date, engine_a_pnl, equity, days=60)

        triggered = (
            hyg_dd <= -self.carry_drawdown_stop
            or credit_z <= self.carry_credit_z_stop
            or pnl_60d <= -self.carry_pnl_budget
        )

        if triggered:
            self.engine_a_shutoff = True
            self.reentry_step = None
        elif self.engine_a_shutoff:
            self.engine_a_shutoff = False
            self.reentry_step = 0

        if self.engine_a_shutoff:
            scale = 0.0
        elif self.reentry_step is not None:
            progress = min(self.reentry_step, self.carry_reentry_weeks) / self.carry_reentry_weeks
            scale = min(self.carry_reentry_start + (1.0 - self.carry_reentry_start) * progress, 1.0)
        else:
            scale = 1.0

        return {s: self.carry_target[s] * scale for s in self.carry_symbols}

    def trailing_pnl_ratio(self, date, current_pnl, equity, days):
        if equity <= 0 or not self.engine_a_pnl_history:
            return 0.0
        # In production use calendar dates. This compact implementation approximates by bars.
        if len(self.engine_a_pnl_history) <= days:
            return 0.0
        baseline = self.engine_a_pnl_history[-days][1]
        return (current_pnl - baseline) / equity

    def engine_b_score(self, symbol, asset_class, region, macro_state, relative_overlay=0.0):
        z = macro_state[region]["z"]
        theme = classify_theme(z)
        factor_vec = np.array([z["growth"], z["inflation"], z["policy"], z["liquidity"], z["credit"]])
        raw = float(np.clip(np.dot(exposure_template(asset_class), factor_vec), -3.0, 3.0))
        coh = coherence(z)
        sign = theme_expression_sign(symbol, asset_class, theme)

        score = 0.0
        if theme != "MIXED" and sign != 0 and sign * raw > 0:
            score = sign * abs(raw)
        if symbol in {"SPY", "EEM", "UUP"} and abs(relative_overlay) > abs(score):
            score = relative_overlay
        return float(np.clip(score * (0.5 + coh), -3.0, 3.0)), coh, theme

    def target_gross(self, score_abs):
        if score_abs < self.probe_threshold:
            return 0.0
        if score_abs < self.conviction_threshold:
            return 0.25 * self.engine_b_max_gross
        if score_abs < self.max_threshold:
            return 0.60 * self.engine_b_max_gross
        return self.engine_b_max_gross

    def build_engine_b_plans(self, macro_state, asset_class_map, region_map, prices):
        rows = []
        for s in self.dislocation_symbols:
            score, coh, theme = self.engine_b_score(
                s,
                asset_class_map.get(s, "EQUITY"),
                region_map.get(s, "US"),
                macro_state,
            )
            if abs(score) >= self.probe_threshold:
                rows.append((s, score, coh, theme))

        rows.sort(key=lambda x: abs(x[1]), reverse=True)
        if not rows:
            return {s: Plan(s, "ENGINE_B") for s in self.dislocation_symbols}

        coh_signal = sum(abs(score) * coh for _, score, coh, _ in rows) / max(sum(abs(score) for _, score, _, _ in rows), EPS)
        extreme_threshold = np.quantile(self.coherence_history, 0.90) if len(self.coherence_history) >= 40 else None
        confirmed = any(st.get("phase", 0) >= 2 for st in self.engine_b_state.values())
        extreme = extreme_threshold is not None and coh_signal >= extreme_threshold and confirmed
        top_n = self.extreme_top_n if extreme else self.engine_b_top_n
        selected = rows[:top_n]
        gross = max(self.target_gross(abs(score)) for _, score, _, _ in selected)
        denom = sum(abs(score) for _, score, _, _ in selected)

        plans = {s: Plan(s, "ENGINE_B") for s in self.dislocation_symbols}
        for s, score, coh, theme in selected:
            raw_weight = gross * abs(score) / max(denom, EPS)
            sign = 1 if score > 0 else -1
            trend_n = self.trend_lookback(s, asset_class_map.get(s, "EQUITY"))
            trend = sma(list(self.closes[s]), trend_n)
            trend_ok = trend is None or (sign > 0 and prices[s] >= trend) or (sign < 0 and prices[s] <= trend)
            trend_mult = 1.0 if trend_ok else 0.50
            core_weight = sign * raw_weight * trend_mult
            plans[s] = Plan(
                symbol=s,
                engine="ENGINE_B",
                target_weight=core_weight,
                core_weight=core_weight,
                score=score,
                coherence=coh,
                reason="DISLOCATION_THEME",
                extreme=extreme,
            )

        self.coherence_history.append(coh_signal)
        return plans

    def trend_lookback(self, symbol, asset_class):
        asset = asset_class.upper()
        if asset in {"CREDIT", "BOND", "DURATION", "TIP", "INFLATION_LINKED_BOND"}:
            return 50
        if asset in {"COMMODITY", "COMMODITY_FUTURE", "ENERGY", "GOLD", "FX"} or symbol in {"USO", "DBC", "UUP"}:
            return 100
        return 200

    def apply_phase_sizing(self, plans, prices):
        for s, plan in plans.items():
            if plan.engine != "ENGINE_B" or plan.core_weight == 0.0:
                continue
            a = atr(list(self.highs[s]), list(self.lows[s]), list(self.closes[s]), 20)
            if a is None:
                continue
            sign = 1 if plan.core_weight > 0 else -1
            state = self.engine_b_state.get(s)
            if state is None or state["sign"] != sign:
                phase = 1
            else:
                bars_held = self.bar_index - state["entry_bar"]
                profit_atr = sign * (prices[s] - state["entry_price"]) / max(a, EPS)
                profit_rate = profit_atr / max(bars_held, 1)
                if bars_held >= self.phase3_min_days and profit_atr >= 3.0 and profit_rate >= 0.12 and plan.coherence >= 0.50:
                    phase = 3
                elif bars_held >= self.phase1_days and profit_atr >= self.phase1_profit_atr:
                    phase = 2
                else:
                    phase = 1

            mult = self.extreme_phase3_mult if phase == 3 and plan.extreme else self.phase_mult[phase]
            plan.phase = phase
            plan.core_weight *= mult
            plan.target_weight = plan.core_weight + plan.reversal_weight
        return plans

    def apply_engine_a_reallocation(self, plans, carry_weights, positions, prices):
        if not self.engine_a_shutoff:
            return plans
        baseline = sum(abs(w) for w in self.carry_target.values())
        current = sum(abs(carry_weights.get(s, 0.0)) for s in self.carry_symbols)
        freed = max(baseline - current, 0.0)
        if freed <= 0:
            return plans

        eligible = []
        for s, p in plans.items():
            if p.engine != "ENGINE_B" or p.phase < 2 or p.core_weight == 0:
                continue
            if positions.get(s, 0) == 0:
                continue
            if np.sign(positions[s]) != np.sign(p.core_weight):
                continue
            room = max(1.0 - abs(p.target_weight), 0.0)
            if room > 0:
                eligible.append((s, abs(p.score), room))
        if not eligible:
            return plans

        extreme_eligible = [x for x in eligible if plans[x[0]].extreme]
        if extreme_eligible:
            s, _, room = max(extreme_eligible, key=lambda x: (abs(plans[x[0]].score), plans[x[0]].phase))
            add = min(freed, room)
            plans[s].core_weight += np.sign(plans[s].core_weight) * add
            plans[s].target_weight += np.sign(plans[s].target_weight) * add
            return plans

        denom = sum(abs(plans[s].core_weight) for s, _, _ in eligible)
        for s, _, room in eligible:
            add = min(freed * abs(plans[s].core_weight) / max(denom, EPS), room)
            plans[s].core_weight += np.sign(plans[s].core_weight) * add
            plans[s].target_weight += np.sign(plans[s].target_weight) * add
        return plans

    def apply_liquidation_reversal(self, plans, prices):
        candidates = []
        next_state = {}
        remaining = self.reversal_max_gross

        for s in self.dislocation_symbols:
            if s == "UUP":
                continue
            closes = list(self.closes[s])
            if len(closes) < 21:
                continue
            plan = plans[s]
            ma10 = sma(closes, 10)
            if ma10 is None:
                continue

            prior = self.reversal_state.get(s)
            if prior:
                bars_held = self.bar_index - prior["entry_bar"]
                valid = (
                    bars_held <= self.reversal_max_hold
                    and prices[s] >= ma10
                    and prices[s] >= prior["entry_price"] * (1.0 - self.reversal_stop_pct)
                )
                if valid:
                    w = prior["weight"]
                    plan.reversal_weight = w
                    plan.target_weight += w
                    next_state[s] = prior
                    remaining = max(remaining - abs(w), 0.0)
                    continue

            dd = drawdown(closes, 21)
            if dd is None or dd > self.reversal_dd_threshold:
                continue
            prior_high = max(closes[-self.reversal_confirm_lookback - 1:-1])
            confirmed = prices[s] > prior_high or prices[s] > ma10
            if not confirmed:
                continue
            impulse = abs(dd) * 0.35
            candidates.append((s, impulse))

        candidates.sort(key=lambda x: x[1], reverse=True)
        for s, impulse in candidates[:self.reversal_top_n]:
            plan = plans[s]
            intensity = clip(impulse / abs(self.reversal_dd_threshold), 0.25, 1.0)
            room = max(self.reversal_max_symbol_weight - abs(plan.target_weight), 0.0)
            add = min(self.reversal_max_symbol_weight * intensity, room, remaining)
            if add <= 0:
                continue
            plan.reversal_weight = add
            plan.target_weight += add
            next_state[s] = {"entry_bar": self.bar_index, "entry_price": prices[s], "weight": add}
            remaining -= add

        self.reversal_state = next_state
        return plans

    def on_bar(self, date, bars, equity, positions, macro_state, asset_class_map, region_map):
        self.bar_index += 1
        prices = {}
        for s, bar in bars.items():
            prices[s] = bar["close"]
            self.highs[s].append(bar["high"])
            self.lows[s].append(bar["low"])
            self.closes[s].append(bar["close"])

        # Engine A
        macro_z = {region: state["z"] for region, state in macro_state.items()}
        carry_weights = self.engine_a_update(date, equity, macro_z, positions, prices)

        # Engine B
        plans = self.build_engine_b_plans(macro_state, asset_class_map, region_map, prices)
        plans = self.apply_phase_sizing(plans, prices)
        plans = self.apply_engine_a_reallocation(plans, carry_weights, positions, prices)
        plans = self.apply_liquidation_reversal(plans, prices)

        # Combine targets
        target_weights = dict(carry_weights)
        for s, p in plans.items():
            target_weights[s] = p.target_weight

        # Convert weights to target quantities
        target_qty = {s: weight_to_qty(equity, prices[s], w) for s, w in target_weights.items() if s in prices}
        return target_qty
```

## 25. Minimal Research Checklist

Before modifying the strategy, require these diagnostics:

1. CAGR, Sharpe, Sortino, Max Drawdown, Calmar.
2. Engine A PnL and Engine B PnL separately.
3. Engine B dormant ratio.
4. Phase 3 active ratio.
5. PnL by phase.
6. PnL by dislocation episode.
7. PnL by instrument.
8. Reallocation active ratio.
9. Liquidation-reversal active ratio and PnL.
10. Profit factor before and after each layer.

Do not promote any modification that improves CAGR only by increasing churn while degrading profit factor materially.

## 26. Research Lessons To Preserve

What worked:

- Barbell split between credit carry/sensor and dislocation engine.
- Concentrating Engine B in top expressions.
- Phase-based sizing.
- Profit-rate Phase 3 promotion.
- Engine A shutoff and capital routing.
- Small stateful liquidation reversal add-on.

What failed or remains experimental:

- Continuous deployment.
- Crisis trend sleeve.
- Dollar squeeze sleeve.
- Transmission overlay as a tiny additive layer.
- Linear convexity proxy.
- Velocity on smooth proxy macro data.

Most likely paths to higher CAGR:

1. First-release point-in-time macro data.
2. Commodity futures instead of ETF proxies.
3. True convex instruments.
4. Re-test velocity with better macro data.
5. Capital-routing overlays rather than additive overlays.
6. Better episode attribution before increasing concentration further.
