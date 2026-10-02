# Trend And Carry Sleeve Results

## Scope

Continued research after the candidate EV gate and conditional hedge tests.

Implemented and tested:

1. `--barbell-trend-sleeve-layer`
2. `--barbell-carry-crash-risk-layer`

Both are kept behind explicit flags and are not promoted into the current research baseline.

## Current Best Baseline

Command:

```bash
MPLCONFIGDIR=/private/tmp/mpl .venv/bin/python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Baseline:

- CAGR: 12.2917%
- Sharpe: 0.9007
- MaxDD: -23.9965%
- Calmar: 0.5122
- PF: 4.4314
- Closed trades: 574

## Time-Series Momentum Sleeve

Design:

- Separate sleeve, not part of Engine B top-N.
- Signal: average sign of 1/3/6/12-month returns.
- Monthly signal refresh.
- Dormant-only activation: active only when Engine B core gross is below 5%.
- Max gross: 12%.
- Max symbol weight: 4%.
- Requires all trend horizons to agree.

Results:

| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 12.2917% | 0.9007 | -23.9965% | 0.5122 | 4.4314 | 574 |
| Trend sleeve initial | 10.0085% | 0.7708 | -23.0239% | 0.4347 | 2.4442 | 5,488 |
| Trend sleeve dormant-only | 12.1043% | 0.8908 | -23.9968% | 0.5044 | 4.0087 | 862 |
| Hedge + trend dormant-only | 12.1143% | 0.8971 | -22.4840% | 0.5388 | 3.8921 | 994 |

Diagnostics for dormant-only trend:

- Active ratio: 36.30%
- Average gross: 3.40%
- Engine B dormant ratio: 46.67%
- Engine B PF: 4.44 versus 4.74 baseline Engine B PF

Decision:

Do not promote. The ETF-proxy trend sleeve is cleaner after tightening, but it still reduces CAGR and PF. This does not reject trend following generally; it rejects this ETF-proxy implementation. A proper futures implementation remains the better research path.

## Carry-Minus-Crash-Risk Layer

Design:

- Separate from the previously rejected dynamic Engine A expansion.
- Does not increase carry in benign regimes.
- Scales HYG/LQD down as credit, liquidity, HYG drawdown, HYG realized vol, or HYG trend stress rises.
- Shifts more weight toward LQD before full carry shutoff.

Command:

```bash
MPLCONFIGDIR=/private/tmp/mpl .venv/bin/python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --barbell-carry-crash-risk-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Result:

| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 12.2917% | 0.9007 | -23.9965% | 0.5122 | 4.4314 | 574 |
| Carry crash-risk | 11.8545% | 0.8738 | -23.9938% | 0.4941 | 4.5197 | 853 |

Decision:

Do not promote. The layer raises PF slightly but lowers CAGR and does not reduce max drawdown. It cuts carry too often without solving the portfolio-level drawdown problem.

## Current Promotion Status

Promote / keep as useful:

- Current research baseline: `--barbell-research-baseline --barbell-liquidation-reversal-layer`
- Conditional hedge layer as optional risk-shaping: `--barbell-conditional-hedge-layer`

Reject for now:

- Candidate EV gate, simple bucketed version
- ETF-proxy trend sleeve
- Carry-minus-crash-risk layer
- Expanded universe as direct Engine B top-N candidates
- Safety/NAV stop bundle
- Full optimization bundle

## Interpretation

The repeated pattern is now clear:

- Anything that dilutes the concentrated dislocation core tends to reduce CAGR and PF.
- Conditional hedging can improve drawdown modestly if it does not compete with Engine B selection.
- ETF proxies are too blunt for trend/rates/commodity carry sleeves.
- The next high-quality research step should improve expression quality, not add more heuristic layers.

## Recommended Next Step

Move to infrastructure-quality expression upgrades:

1. Proper futures-based trend and commodity sleeves.
2. Rates/curve model instead of TLT/IEF/TIP as generic macro instruments.
3. ALFRED/first-release macro surprise data to improve timing and velocity.
4. Conditional options only after linear proxy logic is stable.
