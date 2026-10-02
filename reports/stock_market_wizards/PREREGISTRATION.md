# Stock Market Wizards methods: rules fixed before the first run

Written 2026-10-02, before any backtest of these strategies was run. Every variant listed here
is run once and reported, whatever it shows. No parameter is changed after seeing results.
Where the book gives no number, the number used is stated as "my default" and chosen before
any run.

Already tested in this project and excluded here: Turtle/Dennis breakouts, 12-month trend
following, Paul Tudor Jones' 200-day rule with leverage, O'Neil/Ryan momentum, Minervini's
trend template.

## Common setup

- **Universe:** stocks that are S&P 500 members on the signal date (point-in-time membership,
  `data/index_membership/sp500_membership_pit_panel.csv`) with prices in the S&P 500 panel
  (`examples/fetch_sp500_panel.py`). SPY is the market proxy.
- **Engine:** QuantBT event engine with the conservative-fill fixes from PR #1. Signals on a
  daily close, orders fill at the next open. Engine default costs (5 bps spread, 2 bps
  slippage, 5 bps commission, fees). Cash earns nothing. Margin pays 5%/yr (engine default).
- **Delisting:** a held name with 5 consecutive missing bars is marked dead; its last price
  stays in equity and one sell order is left pending (same rule as PR #6).
- **Periods:** one continuous run from 1998-01 (warm-up) to 2026-09-30. Reported separately:
  in-sample 1999-2019, hold-out 2020-01 to 2026-09, full period. Metrics: CAGR, Sharpe
  (rf = 0), max drawdown, next to SPY total return and the equal-weight panel basket.
- **Success bar:** a hypothesis "passes" only if it beats SPY's CAGR in BOTH periods with no
  worse max drawdown than SPY. Beating SPY by a wide margin (+3%/yr or more) in both is the goal.

## H1. Ahmet Okumus: buy financially sound stocks that are down 60% or more

**Source.** Okumus (chapter "From Istanbul to Wall Street Bull") buys companies with sound
fundamentals that "have declined 60 percent or more off their highs" at P/E under 12, holds
about ten stocks, buys at roughly a 40% discount to his fair value and sells "below fair
value". No stop-loss; he averages down.

**Hypothesis.** Large, index-quality companies that have lost 60%+ of their value are
oversold (long-term reversal, De Bondt & Thaler 1985) and recover enough to beat the index.

**Price-only translation.** No honest point-in-time fundamentals are available (the repo's
fundamentals file is hand-made with hindsight), so S&P 500 membership on the signal date
stands in for "financially sound".

- Signal each day: close <= 40% of the highest close of the last 252 trading days
  (down >= 60% from the 1-year high). Needs 252 bars of history.
- Positions: at most 10 (Okumus holds about ten); each new position gets equity / 10, funded
  from cash. If more candidates than free slots, the deepest decline first.
- Exit: sell when the close reaches 1.5x the entry price. Derivation: buying at a 40% discount
  means fair value is about entry / 0.6 = 1.67x; 1.5x is "selling below fair value" (my default).
- Time stop: sell after 504 trading days (2 years) if the target is not reached (my default).
- Leaving the index does not force a sale (Okumus holds through bad news); no loss stop.

| Variant | Entry: drop from 252-day high | Max positions | Target | Time stop |
|---|---|---|---|---|
| H1-A (headline) | >= 60% | 10 | 1.5x entry | 504 days |
| H1-B | >= 50% ("well over 50 percent") | 10 | 1.5x entry | 504 days |
| H1-C | >= 60% | 20 (equity / 20 each) | 1.5x entry | 504 days |

**Known bias, stated in advance.** This is the hypothesis most flattered by the panel's
missing names: the panel lacks many members that went bankrupt, and those are exactly the
stocks that fell 60%+. 1999-2009 results will be too good; the 2020+ hold-out is the cleaner
test.

## H2. Mark D. Cook: buy the S&P 500 when cumulative breadth is extremely oversold

**Source.** Cook's Cumulative Tick indicator: NYSE tick readings between -400 and +400 are
ignored; readings beyond them are added to a running total. Below the historical 5th
percentile is a buy, above the 95th a sell. Two to four signals a year; he holds a few days
and keeps holding while the signal persists.

**Hypothesis.** Extreme, persistent selling pressure across the index marks short-term lows,
so buying the index on such readings earns more per day invested than holding it.

**Price-only translation.** Intraday NYSE tick data is not available. Daily breadth of the
point-in-time S&P 500 members stands in for it:

- b_t = (advancers - decliners) / (members with a close today and yesterday).
- Ignore quiet days: x_t = b_t if |b_t| > 0.133, else 0. (400 of about 3,000 NYSE issues = 0.133.)
- Running total over the last 21 trading days: C_t = sum of x over 21 days (my default; a
  pure all-history running total drifts and has no stable percentiles).
- Percentiles are computed on C's own history up to t only (expanding window, at least 504
  days), so no look-ahead.
- Buy SPY at the next open when C_t < 5th percentile. Hold while C_t stays below its
  expanding median; sell at the next open after the first close with C_t >= median.

| Variant | When no signal | During a signal |
|---|---|---|
| H2-A (headline) | cash | 100% SPY |
| H2-B | 100% SPY | 200% SPY (margin at 5%/yr) |
| H2-C | 100% SPY; sell to cash when C_t > 95th percentile until C_t <= median | 100% SPY |

## H3. David Shaw / Steve Cohen: short-term reversal in large caps

**Source.** Shaw (D. E. Shaw, "statistical arbitrage") describes exploiting many small,
short-lived price inefficiencies that revert; Cohen's desk traded short-term overreactions in
liquid stocks. The published rule-based form is the weekly contrarian strategy of Lehmann
(1990) and the monthly reversal of Jegadeesh (1990).

**Hypothesis.** Large caps that fell the most in the last week (month) rebound the next week
(month) by more than trading costs.

- Signal on the close of the last trading day of each week (H3-A) or month (H3-C).
- Rank members by trailing return: 5 trading days (weekly) or 21 trading days (monthly).
- Hold the bottom decile (worst performers), equal weight, until the next signal. Positions
  still in the bottom decile are kept; others are sold; new names get equity / N.
- No stop, no market filter.

| Variant | Lookback / rebalance | Book |
|---|---|---|
| H3-A (headline) | 5 days / weekly | long bottom decile |
| H3-B | 5 days / weekly | long bottom decile, short top decile (dollar neutral), vector simulator, 10 bps per side, no borrow fee |
| H3-C | 21 days / monthly | long bottom decile |

Expectation stated in advance: H3 is the most likely to fail after costs, because weekly
turnover is near 100% and the effect has been widely traded since the 1990s.

---

## Follow-up (added 2026-10-02 after the nine runs above, before any run of these)

**Why a follow-up.** H2's buy signal held up out of sample, but the 2x overlay (H2-B) deepened
drawdowns. These variants change only the overlay size and add one trend condition. The
signal itself (breadth proxy, 21-day sum, 5th percentile entry, median exit) is unchanged,
so nothing here is fitted to the signal. Because they were chosen after seeing H2-B, they
are a second look at the same signal, not independent evidence; the 2020+ hold-out has
already been seen once.

All hold 100% SPY when no buy signal is on. During a buy signal:

| Variant | Exposure on signal | Condition |
|---|---|---|
| H2-D | 1.25x SPY | none |
| H2-E | 1.5x SPY | none |
| H2-B (already run) | 2.0x SPY | none |
| H2-F | 1.5x SPY | only while SPY's close is above its 200-day SMA, else 1.0x |
| H2-G | 2.0x SPY | only while SPY's close is above its 200-day SMA, else 1.0x |

The 200-day condition is checked on every close during a signal, so exposure can step
between 1x and the overlay inside one signal. Margin at 5%/yr, engine default costs.
Same pass bar as above: beat SPY's CAGR in both periods with no worse max drawdown.
