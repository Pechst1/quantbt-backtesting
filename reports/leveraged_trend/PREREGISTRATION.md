# Leveraged index + 200-day trend filter: pre-declared test plan

Written and committed before any backtest of these rules was run. Every variant listed
here is reported in README.md, whatever it shows. Nothing is tuned to the results.

## Idea

Paul Tudor Jones (Market Wizards; later interviews): "My metric for everything I look at
is the 200-day moving average of closing prices. ... I don't want to be long anything
that's below it." Applied to an index: hold it while it closes above its 200-day simple
moving average, go to T-bills when it closes below. Because the filter tends to sidestep
the deepest bear markets (where daily-reset leverage does the most damage), it may make
leverage survivable. That is the hypothesis being tested.

## Instruments

- Underlyings: SPY and QQQ total-return series (adj_close from the public S&P 500 panel,
  Johnbrick123/sp500-data). No other underlyings.
- Leverage: 1x, 2x, 3x. Leveraged series are synthetic daily-reset ETFs built from the
  underlying, like SSO/UPRO/QLD/TQQQ:
  `r_L = L * r_index - (L - 1) * (tbill + 0.50%) / 252 - 0.95% / 252` for L > 1,
  where `tbill` is FRED DTB3. 0.95% is the ProShares/Direxion expense ratio; 0.50% is a
  swap-financing spread over T-bills. 1x uses the plain ETF (its fee is already in price).
  The synthetic series are checked against the real UPRO/SSO/TQQQ/QLD where data exists
  (Massive, 2021-10 onward).

## Rules (two per instrument)

1. Buy-and-hold: all equity in the instrument from the first trading day.
2. 200-day filter: at each close, compare the underlying's close with its 200-day simple
   average of closes. Above: hold the (leveraged) instrument. Below or equal: hold cash
   earning DTB3 minus 0.10% (a T-bill ETF fee). Orders fill at the next day's open through
   the QuantBT engine with its default costs (5 bps spread, 2 bps slippage, 5 bps
   commission, SEC/exchange fees). No bands, no confirmation days, no re-entry delay.

2 underlyings x 3 leverage levels x 2 rules = 12 variants. Benchmark: SPY buy-and-hold.

## Periods

- In-sample: 1999-01-01 to 2019-12-31 (QQQ starts trading 1999-03-10, so its filter needs
  200 days of history; QQQ variants are scored from the first day all are live).
- Hold-out: 2020-01-01 to 2026-09-30.
One continuous simulation, sliced into the two windows; full period also reported.

## Metrics

CAGR, annualized volatility, Sharpe (excess over DTB3), max drawdown, longest drawdown,
number of switches, time invested.

## Robustness only (not for selection)

For the 3x variants, the filter length is also run at 100, 150 and 250 days to show
whether the result hinges on 200. The headline stays the published 200-day rule.

## Known limits, stated up front

- 2x/3x index ETFs only exist from 2006 (SSO/QLD) and 2009-2010 (UPRO/TQQQ). Results
  before that are hypothetical.
- Signals use adjusted closes, so ex-dividend days shift the average by a few bps.
