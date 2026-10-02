# Robust Macro Redesign Evaluation

## Decision

The legacy 16.03% CAGR result is rejected as a robust estimate. After correcting
the public-series config to request ALFRED `output_type=4` initial releases and to
stop treating FRED query dates as historical release dates, the same legacy logic
produces -1.42% CAGR and a -55.36% maximum drawdown.

The robust redesign removes macro-factor direction from the trading decision.
Volatility-normalized price evidence determines direction; macro data is retained
only for diagnostics. Cheap Discovery sizes unconfirmed Engine B Phase-1 probes at
15% of target and preserves full Phase-2/3 sizing after price confirmation.

## Results, 2005-01-01 to 2025-01-01

| Variant | CAGR | Sharpe | MaxDD | Profit factor | Closed trades |
|---|---:|---:|---:|---:|---:|
| Legacy current best / legacy macro | 16.03% | 1.093 | -22.66% | 5.274 | 1,109 |
| Legacy current best / corrected public macro | -1.42% | -0.059 | -55.36% | 0.899 | 1,646 |
| Market evidence, stateful core | 3.98% | 0.334 | -42.21% | 1.360 | 1,159 |
| Market evidence plus trend | 4.50% | 0.367 | -44.16% | 1.350 | 1,153 |
| Robust sleeves, Engine B disabled | 3.11% | 0.465 | -15.40% | 1.582 | 1,989 |
| Robust redesign plus Cheap Discovery | 6.42% | 0.522 | -38.52% | 1.746 | 1,453 |

The final row is exactly identical with legacy and public macro snapshots. It is
therefore robust to the disputed macro mapping, but its return/risk profile is not
production quality.

## Added Public Information

The public macro builder now distinguishes two data classes:

- Rewritable economic releases use ALFRED initial-release observations
  (`output_type=4`) and `realtime_start` as their first public release date.
- Non-revised market sensors use FRED observations and conservative observation-
  specific lags instead of the API query's `realtime_start`.

The official EIA WTI Contract 1-4 daily histories are also ingested into a genuine
term-structure dataset. One-month carry is annualized from Contract 1 versus 2;
three-month carry is annualized from Contract 1 versus 4. Calendar spreads use an
absolute denominator so the negative April 2020 front contract remains valid.

Two clean additions were rejected:

| Addition | CAGR | Sharpe | MaxDD | Profit factor | Decision |
|---|---:|---:|---:|---:|---|
| EIA WTI curve, fixed notional sizing | 5.01% | 0.382 | -41.38% | 1.462 | Reject |
| EIA WTI curve, 4% annual-vol risk sizing | 6.19% | 0.501 | -38.64% | 1.713 | Reject |
| Corrected public release-innovation timing | 5.99% | 0.492 | -38.77% | 1.671 | Reject |

## Implemented Logic

For lookback `h` in `{21, 63, 126, 252}`:

```text
r_h = log(C_t / C_{t-h})
z_h = clip(r_h / (sigma_63 * sqrt(h)), -3, 3)
agreement = abs(sum(sign(z_h))) / 4
market_score = 1.5 * (0.10*z_21 + 0.20*z_63 + 0.30*z_126 + 0.40*z_252)
               * (0.65 + 0.35*agreement)
```

A new Engine B position requires aligned abnormal 21-day and 63-day evidence.
An existing position may remain open when score direction agrees, absolute score
is at least 0.35, and horizon agreement is at least 0.50.

Phase sizing:

```text
Phase 1: 0.15 * target, 1.5 ATR stop
Phase 2: 1.00 * target, 2.5 ATR trailing stop
Phase 3: 1.30 * target, or 1.50 in extreme regimes, 3.5 ATR trailing stop
```

Market-evidence Phase 3 additionally requires absolute score at least 2.50 and
agreement at least 0.75.

## Attribution

In the promoted robust variant:

| Sleeve | Net PnL | Profit factor |
|---|---:|---:|
| Engine B | 465,078 | 1.770 |
| Rates | 105,404 | 1.716 |
| Commodity futures | 62,185 | 2.749 |
| Engine A carry | 40,272 | 1.788 |
| Trend sleeve | -13,753 | 0.701 |

Phase-1 trades still lose 261,599 with PF 0.117. Phase-3 trades earn 625,316 with
PF 7.505. Cheap Discovery improves payoff geometry but does not create sufficient
entry alpha.

## Research Conclusion

Do not increase leverage or retune thresholds. The remaining problem is signal
quality before Phase 2. The next credible work is an independently specified,
point-in-time event-surprise model or true futures term-structure/carry model,
validated as a standalone sleeve before portfolio integration. The 16% legacy
result must not be used for capital allocation.
