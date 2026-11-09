# Vol-targeted trend following: settings declared before the first run

Written and committed before any vol-targeted backtest was run. Nothing below may
change after results are seen; every variant listed here is reported.

## Systems (unchanged published rules)

- Turtle System 1 and System 2 (Curtis Faith, "The Original Turtle Trading Rules"):
  breakout entries, channel exits, 2N stops, 1/2 N pyramiding to 4 units, unit limits.
  Code: `quantbt.systems.turtle`, 14-ETF universe `TURTLE_UNIVERSE`.
- 12-month time-series momentum (Moskowitz, Ooi, Pedersen 2012): sign of 12-month
  excess return, 40% / sigma per instrument, equal weight, monthly rebalance.
  Code: `quantbt.systems.tsmom`, 25-ETF universe `TSMOM_UNIVERSE`.

## Portfolio risk overlay (the only change)

The published rules define a "native" book. The account holds `k` times that book.

- Volatility target: **20% a year** (primary) and **15% a year** (secondary). Both reported.
- Vol estimate: zero-mean EWMA of the native book's daily return, center of mass
  60 days, annualised with 261 days (the TSMOM paper's estimator). No trading until
  60 daily returns exist (warm-up falls in 2004-2007, before scoring starts).
- `k = target / estimated native vol`, set at each close and applied at the next open.
- **Hard gross leverage cap: 3.0x equity.** The open rebalance scales `k` down so
  gross <= 3x, and every intraday entry or add is trimmed so gross stays <= 3x.
- Turtle under the overlay: units are still 1% of equity per N, and the overlay
  replaces the Turtle drawdown rule (cut notional 20% per 10% loss), since both scale
  the whole book by account risk. Native unit sizes are fractional.
- The book is rescaled to `k` every day at the open; those trades pay costs too.

## Costs and financing (all runs, native and overlay)

- 9.5 bps per side on every trade (as in the earlier Turtle/TSMOM runs).
- Cash earns the BIL total return; negative cash pays BIL + 50 bps a year.
- Short proceeds earn the bill rate; no borrow fee (stated limitation).

## Data and windows

- Yahoo daily bars, total-return adjusted, requested from 2004-01-01.
- Scored from 2008-01-01. In-sample 2008-01-01 to 2019-12-31; hold-out 2020-01-01 to the
  last available bar (2026-09-30).
- Benchmark: SPY total return, buy and hold, same windows.

## Variants reported

Turtle S1, Turtle S2, Turtle 50/50 and TSMOM, each at native published sizing, at
15% vol target and at 20% vol target, all with the 3x cap on the overlay runs. 12 runs
plus SPY. No other variant will be run for this report.
