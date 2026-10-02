# Market Wizards (1989): three untested traders' methods, rules fixed before the first run

Written 2026-10-02, before any backtest in this folder was run. Every variant listed here is
run once and reported, whatever it shows. No parameter is changed after seeing results.
Parameters come from the traders' own published statements, not from this data.

Already tested elsewhere in this repo and skipped here: Dennis/Turtle breakouts, 12-month
trend following, Paul Tudor Jones's 200-day rule with leverage, O'Neil/Ryan momentum,
Minervini trend template.

## Hypothesis 1: Marty Schwartz, "red light, green light" (10-day EMA)

Source: Schwartz in *Market Wizards* (1989) and *Pit Bull* (1998): "The 10 day exponential
moving average is my favorite indicator to determine the major trend ... When you are trading
above the 10 day, you have the green light ... below the average is a red light." He traded
S&P 500 futures, long and short.

**Hypothesis:** short-term index trend persists, so being long only above the 10-day EMA
(and short below it) beats buy-and-hold on return or risk-adjusted return.
**Prior:** weak. Daily S&P 500 autocorrelation turned negative after the late 1990s, so a
10-day rule may have worked in Schwartz's era (1980s) and not since.

Rules:
- EMA of the adjusted close with alpha = 2 / (10 + 1). Signal at each close; the position
  changes at the next open.
- S1: SPY long above the EMA, 3-month T-bills below it.
- S2: SPY long above, short SPY (1x) below.
- S3: QQQ long above, T-bills below (second index, from 1999-03).

## Hypothesis 2: Ed Seykota exponential crossover, sized with Larry Hite's risk rule

Source: Seykota's own Trading System Project (seykota.com/tribe/TSP, "EA" system, verified
by readers to the penny in 2005): S&P, fast EMA lag 15, slow EMA lag 150, long only, buy at
the next open after the fast crosses above the slow, sell at the next open after it crosses
below. Position size = heat x equity / (ATR multiple x ATR), heat 10%, ATR multiple 5,
ATR lag 20 (exponential, alpha = 2/(lag+1)), set at entry and not resized during the trade.
Fills use 50% "skid": buys at open + 0.5 x (high - open), sells at open - 0.5 x (open - low).
Larry Hite (*Market Wizards*): "never risk more than 1% of total equity on any trade",
spread across many markets.

**Hypothesis:** Seykota's crossover with volatility sizing beats SPY on SPY itself, and the
same rule spread across individual large caps with Hite's 1% risk per position beats it by
more, because single stocks trend more than the index.

Rules:
- K1 (published TSP): SPY, 15/150, heat 10%, 5 x ATR20, skid 50%, no commission, no
  leverage cap (borrowing pays T-bill + 1%).
- K2: K1 with gross exposure capped at 1.0x (no borrowing).
- K3 (Seykota + Hite on stocks): every point-in-time S&P 500 member with 150+ bars whose
  EMA15 > EMA150 on the signal date is held, long only. Target weight per stock =
  1% / (5 x ATR20 / close). If the sum of weights exceeds 1.0 they are scaled down
  proportionally (no leverage). Signal on each week's first trading day close, rebalanced
  to targets at the next open. Members leaving the index are sold at the next rebalance.
  Costs 10 bps per side.

## Hypothesis 3: Jim Rogers, buy what is depressed once positive change shows

Source: Rogers in *Market Wizards*: he buys markets that are cheap and depressed where he
sees "positive change" coming, and is wary of whatever everyone loves. He invested across
countries. Turned into price rules: "depressed" = worst trailing multi-year return; "positive
change" = price back above its 10-month average (Faber's published 10-month rule).
Horizons are the published ones: 5 years for country indexes (Balvers, Wu & Gilliland 2000,
country index mean reversion) and 3 years for single stocks (De Bondt & Thaler 1985).

**Hypothesis:** depressed markets that have turned up outperform SPY.

Rules (signal at each month's last close, trade at the next open, 10 bps per side, idle
slots earn T-bills):
- Country universe: SPY plus iShares country ETFs EWA EWC EWD EWG EWH EWI EWJ EWK EWL
  EWM EWN EWO EWP EWQ EWS EWU EWW (1996), EWZ EWY EWT (2000), EZA (2003). A fund is
  eligible once it has 60 months of history.
- R1: rank eligible funds by 60-month total return; take the 4 lowest; each is held at 25%
  only if its month-end close is above its 10-month average of month-end closes,
  otherwise that 25% sits in T-bills.
- R2: the same 4 lowest, no positive-change filter.
- R3 (control, not a hypothesis): equal weight across all eligible funds, monthly.
- R4 (stocks): point-in-time S&P 500 members; rank by 36-month total return; take the
  20 lowest; each held at 5% only if above its 10-month average, else T-bills.

## Common rules

- Data: Yahoo Finance adjusted OHLC for ETFs (SPY, QQQ, country funds); the survivorship-
  aware S&P 500 panel (`examples/fetch_sp500_panel.py`) for stocks, with open/high/low
  adjusted by adj_close / close. Cash yields FRED DTB3 (3-month T-bill).
- Costs: 10 bps per side on traded value (the repo engine's default is about 9.5 bps),
  except K1/K2 which use Seykota's skid instead. Shorts pay 0.5%/yr borrow.
- Execution: the signal uses data up to the close of day t; trades fill at the open of
  day t+1. No same-close fills (as in the PR #1 engine fixes).
- A stock whose prices end (delisting) is sold at its last close with costs.
- Periods: each strategy from its first full year with signals to 2019 (in-sample) and
  2020-01 to 2026-09 (hold-out), next to SPY over the same dates. Metrics: CAGR, Sharpe
  (rf = 0, and excess over T-bills), max drawdown.

## Known caveats (stated up front)

- The stock panel misses about a third of historical members (mostly pre-2010 delistings);
  an equal-weight basket of covered names beats RSP by about 2.2%/yr in 2003-09 and
  0.7-0.9%/yr after 2010. This flatters K3 and especially R4 (losers that went bankrupt
  are the ones most likely to be missing).
- Country ETFs before about 2005 had wide spreads; 10 bps understates their cost.
