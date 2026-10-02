# Pre-registration: Market Wizards: The Next Generation (Schwager & Coyle, June 2026)

Written and committed before any backtest of these rules was run. Every variant listed here
will be reported, whatever it shows. No parameter will be changed after the first run; any
later idea gets its own new pre-registration.

## Which traders, and why only one is testable

The book (Harriman House, 9 June 2026) interviews young, mostly self-taught traders. The ones
named in public sources: Kristjan Kullamägi (Qullamaggie), Lance Breitstein, "TheShortBear",
Kelvin Chiu, KQ, Phil Goedeker, Lukas Frohlich.

* **Kullamägi** is the only one with publicly written, numeric rules. He trades exactly three
  setups (breakout, episodic pivot, parabolic short), risks about 1% of the account per trade,
  wins only 25-30% of trades and makes his money from a few large winners (Coyle in the book's
  publicity; his own blog posts and stream notes). His two long setups are tested here.
* **Breitstein** trades intraday capitulation reversals ("right side of the V") with a
  discretionary checklist (VWAP, tape, 5- and 15-minute alignment). There is no daily-bar rule
  set to test, and the daily cousins (Parker RSI(2) dip buying, Cook breadth capitulation,
  weekly reversal) were already tested in PRs #10 and #11.
* The parabolic short (Kullamägi, TheShortBear) is skipped: shorting needs borrow and cost data
  for small caps we do not have, and the panel lacks the small caps where it happens.
* Chiu (agricultural futures options), Goedeker, Frohlich, KQ: discretionary or no published rules.

## Hypotheses

**H1 (breakout).** Buying a tight consolidation breakout in a stock that is among the top 2% of
performers over 1, 3 or 6 months, with 1% risk per trade, a partial sale after 3 days and a
moving-average trail, beats SPY after costs in both the in-sample and the hold-out period.

**H2 (episodic pivot).** Buying the day after a gap of 10% or more on heavy volume that held into
the close, with the gap-day low as the stop and the same exits, beats SPY after costs in both periods.

Null for both: CAGR at or below SPY in either period.

## Data and periods

* `SPX`: point-in-time S&P 500 members from the GitHub panel of PR #4 (830 of 1,222 historical
  members, survivorship bias about +1-2%/yr measured in PR #4). In-sample 1999-07-01..2019-12-31,
  hold-out 2020-01-01..2026-09-30. QQQ starts 1999-03.
* `US`: all US common stocks (Massive grouped daily bars, active and delisted tickers of type CS),
  2021-10-04..2026-09-30, scored from 2022-04-01 after warm-up. Eligible on a day: close >= $5
  and 20-day average dollar volume >= $5M. This is the universe Kullamägi actually trades
  (small and mid caps); it only exists inside the hold-out window, so it is a hold-out-only check.

## Shared execution rules

* Engine from PRs #1/#4/#12: next-bar fills only, day stop orders, gaps fill at the open,
  ~9.5 bps per side (2.5 half spread + 2 slippage + 5 commission), idle cash earns the 3-month
  T-bill (FRED DTB3), no leverage.
* Sizing: shares = 1% of equity / (planned entry - initial stop), capped at 25% of equity and
  by available cash.
* Market filter (breakouts only): QQQ 10-day EMA above its 20-day EMA at the signal close.
  EPs are catalyst trades and are taken without it.
* Exits, identical for both setups:
  1. Protective stop as a day stop order from the bar after entry.
  2. After the third completed bar following the entry bar, sell one third at the next open
     and raise the stop on the rest to the entry price.
  3. Sell the rest at the next open after the first close below the N-day SMA
     (N = 10 or 20 by variant).
  4. A held symbol whose data ends is sold at the next real open.
* Known optimism: on the entry bar itself the stop is not active (the engine cannot place a
  stop on the bar an entry fills), so a same-day round trip to the stop is not modelled.

## Breakout setup (signal at close t, entry t+1)

1. Eligible on t (S&P member, or US liquidity screen).
2. Return over 21, 63 or 126 bars in the top 2% of the eligible universe that day.
3. ADR(20) >= 4%, ADR = mean of high/low - 1 over 20 bars (variant C drops this rule:
   few S&P 500 stocks ever reach 4%).
4. Close above its 10-, 20- and 50-day SMA ("surfing" the averages).
5. Prior move: close / lowest low of the last 63 bars - 1 >= 30%.
6. Consolidation: pivot P = highest high of the last 10 bars, set at least 3 bars before t;
   L = lowest low of the last 3 bars; P - L <= 1.2 x ADR(20) x P.
7. Entry: buy stop at P, good for day t+1 only. Initial stop L, raised to the entry-bar low
   if that is higher.
8. If more setups than cash: highest 63-bar return first.

## Episodic pivot setup (signal at close t, entry at the open of t+1)

1. Eligible on t.
2. Gap: open_t / close_(t-1) - 1 >= 10%.
3. Volume_t >= 3 x average volume of the prior 20 bars.
4. Held the gap: close_t >= open_t.
5. Risk: close_t - low_t <= 1.5 x ADR(20, through t-1) x close_t.
6. Entry: market on open t+1. Initial stop low_t. Sizing uses close_t - low_t.
7. If more setups than cash: largest gap first.

## Variants (all reported)

| Name | Universe | Setup | ADR >= 4% | Trail SMA |
|---|---|---|---|---|
| KB-A10 | SPX | breakout | yes | 10 |
| KB-A20 | SPX | breakout | yes | 20 |
| KB-C10 | SPX | breakout | no | 10 |
| EP-10 | SPX | episodic pivot | n/a | 10 |
| EP-20 | SPX | episodic pivot | n/a | 20 |
| US-KB-A10 | US | breakout | yes | 10 |
| US-EP-10 | US | episodic pivot | n/a | 10 |

## Reporting and pass bar

CAGR, Sharpe (excess of T-bills), max drawdown, exposure, trade count, win rate and average
win/loss, in-sample and hold-out, next to SPY over the same days. A variant "passes" only if it
beats SPY's CAGR in both SPX periods (US variants: in their window) without a deeper max
drawdown than SPY's in the same period.
