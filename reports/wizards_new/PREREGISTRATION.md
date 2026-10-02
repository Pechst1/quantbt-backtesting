# Pre-registration: three untested Market Wizards methods

Written 2026-10-02, before any backtest of these rules was run. Every variant listed here is
reported in `README.md`, whatever it shows. Parameters come from the published sources named
below; none are chosen by looking at results.

Already tested in this project and therefore skipped: Turtle/Dennis breakouts, 12-month trend
following, the 200-day rule with leverage, O'Neil/Ryan momentum, the Minervini template.

## Shared setup

- **Prices.** S&P 500 panel from PR #4 (`examples/fetch_sp500_panel.py`), point-in-time
  membership, dividend/split-adjusted OHLC (open/high/low scaled by `adj_close / close`).
  ETFs and indices (^VIX, ^VIX3M, ^GSPC, ^IRX, VIXY, SVXY) from Yahoo Finance.
- **Timing.** Signals use data up to the close of day t. Stock trades fill at the open of
  t+1. ETF strategies (H2) switch at the close of t+1, i.e. one full day of delay, because the
  VIX prints after the ETFs close.
- **Costs.** 10 bps per side on every trade (the engine default is about 9.5 bps). Idle cash
  earns the 3-month T-bill rate (^IRX, previous day's value). Short positions pay 0.5%/yr borrow.
- **No leverage** except H3's dollar-neutral long/short book (100% long + 100% short).
- **Periods.** In-sample 1999-01-01 to 2019-12-31 (H2: 2011-02-01 to 2019-12-31, the VIXY
  history limit). Hold-out 2020-01-01 to 2026-09-30. Each strategy runs once, continuously;
  period stats are slices of the same equity curve.
- **Reported per period:** CAGR, Sharpe (rf = 0, daily, ×√252), max drawdown, next to SPY total
  return and (for stock strategies) an equal-weight basket of covered members.
- **Success bar:** beats SPY's CAGR by at least 3 points a year in *both* periods, with a
  Sharpe at least as high as SPY's. Anything else is reported as a failure.

## H1. Marsten Parker short-term mean reversion (Unknown Market Wizards, 2020)

Parker's chapter describes his long mean-reversion systems as: buy a stock that is in an
uptrend but has pulled back sharply, no stop-loss ("a stop will kill the results"), take the
small win on the first up close, and use a 5-day time stop. He does not publish the exact
pull-back trigger, so the trigger is Larry Connors' published RSI(2) rule (Connors & Alvarez,
*Short Term Trading Strategies That Work*, 2008): close above the 200-day SMA and 2-period
Wilder RSI below 5 (10 for the looser version). Exit per Connors: first close above the 5-day
SMA.

**Hypothesis:** in S&P 500 stocks, sharp 2-3 day sell-offs inside long-term uptrends reverse
quickly, so buying them and exiting on the bounce earns more than SPY with lower exposure.

Rules: universe = point-in-time S&P 500 members with at least 200 days of history. Each day
after the close, candidates are members with close > SMA200 and RSI(2) < threshold, not already
held. Rank by RSI(2) ascending, fill free slots at next open, each new position gets
1/slots of current equity (skip if cash is short). No stop-loss. A held stock that leaves the
data is sold at its last close.

| Variant | Entry | Exit | Slots |
|---|---|---|---|
| A | RSI(2) < 5 | close > SMA5, sell next open | 10 |
| B (Parker exit) | RSI(2) < 5 | first close above the prior close, or 5 days held; sell next open | 10 |
| C | RSI(2) < 10 | close > SMA5 | 10 |
| D | as A, new entries only while SPY close > SPY SMA200 | close > SMA5 | 10 |
| E | as A | close > SMA5 | 20 |

Cost sensitivity, declared now: variant A is also rerun at 20 bps per side.

Known bias: the panel misses about a third of historical members, mostly names that were
acquired or went bust. Dip-buying is the strategy most flattered by that, because the dips
that never recovered are under-represented. The equal-weight panel benchmark carries the same
bias, and the hold-out (bias about +0.9%/yr) is the cleaner test.

## H2. Volatility risk premium with Tony Cooper's timing rules

Thorp's original edge (in *Hedge Fund Market Wizards* and *Beat the Market*) was selling
options priced above their fair value. The modern listed version is shorting VIX futures, which
collects both the variance premium (VIX above realized volatility) and the roll yield (contango).
Timing rules are taken unchanged from Tony Cooper, *Easy Volatility Investing* (2013):

- Roll-yield rule: 10-day SMA of VIX3M/VIX > 1 → short volatility, else long volatility.
- VRP rule: 5-day SMA of (VIX − HV10) > 0 → short volatility, else long volatility.
  HV10 = 10-day standard deviation of S&P 500 daily log returns × √252 × 100.

Instruments: short volatility = daily −1× VIXY return minus a 1.35%/yr fee (a rebuilt XIV,
which was itself a daily −1× note on the same futures index). Long volatility = VIXY. The real
SVXY (−1× until 2018-02-27, −0.5× after) is used as a cross-check of buy-and-hold.

**Hypothesis:** the premium is large enough that short volatility, switched off (or flipped to
long volatility) when the term structure inverts or implied falls below realized, beats SPY.

| Variant | Rule | When off |
|---|---|---|
| V1 | always short vol (buy-and-hold XIV rebuild) | — |
| V2 | roll-yield rule | long VIXY |
| V3 | VRP rule | long VIXY |
| V4 | roll-yield rule | T-bills |
| V5 | VRP rule | T-bills |
| V6 | VRP rule, half size (−0.5×, like today's SVXY), rest in T-bills | T-bills |

Crash risk is part of the result: the report shows the worst single day and the 2018-02-05
and 2020-02/03 episodes for each variant.

## H3. Ed Thorp's statistical arbitrage (Hedge Fund Market Wizards, 2012)

Thorp describes his stat-arb as ranking stocks by how much they moved over the recent past,
buying the biggest losers and shorting the biggest winners, market-neutral, with roughly
two-week horizons (the "MUD" project at Princeton Newport, later Ridgeline). The academic
version with fixed parameters is Lehmann (1990): weekly reversal.

**Hypothesis:** short-term (one-week) relative moves in large caps reverse, so a dollar-neutral
book long the week's losers and short the week's winners earns a positive return
uncorrelated with SPY.

Rules: on the last trading day of each week, rank point-in-time members (at least 60 days of
history) by 5-day return minus SPY's 5-day return. Long the bottom group, short the top group,
equal weight, 100% of equity on each side, entered at the next open and held to the next
rebalance. Equity earns T-bills; shorts pay 0.5%/yr borrow.

| Variant | Groups |
|---|---|
| S1 | bottom / top decile, long/short |
| S2 | bottom / top quintile, long/short |
| S3 | bottom decile, long only (no short leg) |
