# Megacap reversal + Cook breadth + trend filter: rules fixed before the first run

Written 2026-10-02, before any backtest of these variants was run. Every variant and account
size listed here is run once and reported, whatever it shows. No parameter is changed after
seeing results. Where no published number exists, the value is marked "my default" and was
chosen before any run.

## Why this test, and what it already knows

This is not independent evidence. It combines three pieces that earlier threads already ran
on the same data, chosen *because* of those results:

- **Core (PR #10, H4 R4):** long-only, buy last week's worst S&P 500 members (5-day return minus
  SPY, bottom decile), hold 4 weeks in 4 overlapping weekly cohorts. 12.7% / 19.8% a year at
  2 bps per side (1999-2019 / 2020-2026), 6.7% / 13.3% at 25 bps; −71% drawdown in 2008.
- **Booster (PR #11, H2):** Mark Cook's cumulative-breadth buy signal on S&P 500 members
  (83%+ winning signals, p ≈ 0.0003 vs random holding periods). It fires mostly when the
  market is already below its 200-day average.
- **Crash protection:** a standard trend filter (Paul Tudor Jones / Faber 200-day rule, or
  12-month time-series momentum, Moskowitz-Ooi-Pedersen 2012).

What is new is (a) the universe is cut to the largest 50-100 members, where Scalable Capital's
half-spread is about 5-7 bps, and (b) the costs are Scalable's real fee structure: EUR 1.99 per
order plus 6 bps half-spread, at fixed account sizes. Because the 2020+ hold-out has already
been seen by the component tests, it is a weaker test here than it was there.

## Common setup

- **Data:** survivorship-aware S&P 500 panel (`examples/fetch_sp500_panel.py`, PR #4) with
  point-in-time membership; SPY total return and ^IRX (T-bills) from Yahoo.
- **Universe on each formation date:** point-in-time S&P 500 members with at least 63 trading
  days of history, ranked by average daily dollar volume (close x volume) over the last 63
  trading days (my default; a point-in-time market cap is not in the panel). The top N form
  the megacap universe. N = 100 (headline) or 50.
- **Core signal (unchanged from R4):** on the last trading day of each week, rank the megacap
  universe by 5-day return minus SPY's 5-day return. The bottom decile (N/10 names: 10 for
  N = 100, 5 for N = 50) is that week's cohort. The book holds the last 4 cohorts, each with
  1/4 of the capital, equal weight inside a cohort; a name in several cohorts gets the sum.
  Trades at the next open. Prices are split- and dividend-adjusted.
- **Rebalancing band (my default):** a name held before and after a rebalance is traded only
  if its value differs from its target by more than 25% of the target. New names are bought,
  dropped names are sold in full. This keeps the order count (and the fixed fee) down.
- **Exposure overlays** (decided on closes, traded at the next open):
  - *Trend 200d:* SPY total-return close above its 200-day simple average. Checked on the
    weekly formation close only (to avoid daily whipsaw orders).
  - *Trend TSMOM:* SPY's 252-day total return above the T-bill return over the same 252 days.
    Checked weekly, as above.
  - *Cook buy state:* exactly PR #11's definition. Daily breadth b = (advancers − decliners) /
    members over all point-in-time S&P 500 members; |b| <= 400/3000 counts as 0; C = sum over
    21 days; percentiles of C over its own past (expanding, at least 504 values). A buy state
    starts when C < 5th percentile and ends on the first close with C >= median. Checked
    daily, because the signal is short-lived and the point is buying near the low.
  - When the book goes from out to in, all 4 cohort slots are filled with the bottom decile
    ranked on that close. When it goes from in to out, everything is sold at the next open.
- **Idle cash** earns T-bills. Borrowing (only M4) costs T-bills + 3%/yr (my default; a
  Lombard credit rate).
- **Costs (Scalable Capital, checked 2026-10-02 in PR #10):** EUR 1.99 per order (buy or sell)
  plus 6 bps half-spread on traded notional. Account sizes EUR 10k, 25k, 50k and 100k.
  Returns are computed in the stocks' own currency; EUR/USD moves are ignored (they hit SPY
  equally). Fractional shares are assumed.
- **Periods:** two separate runs, each starting at the account size: in-sample 1999-01-01 to
  2019-12-31 and hold-out 2020-01-01 to 2026-09-30 (indicator warm-up uses earlier data). Each
  run starts fully invested at its first close if its overlay says "in".
- **Benchmarks (no costs):** SPY total return; equal-weight basket of the same top-N megacaps,
  re-formed and rebalanced weekly.
- **Metrics:** CAGR, Sharpe (rf = 0), max drawdown, worst calendar year, average exposure,
  orders per year, fees and spread paid per year as % of equity.

## Variants

| Variant | Universe | Trend filter | Cook | Leverage |
|---|---|---|---|---|
| M1 | top 100 | none (always in) | no | 1.0x |
| M2 | top 100 | 200d | no | 1.0x |
| **M3 (headline)** | top 100 | 200d | in while Cook buy, even if trend is off | 1.0x |
| M4 | top 100 | 200d | as M3, and 1.5x gross while Cook buy | 1.5x on signal |
| M5 | top 50 | 200d | as M3 | 1.0x |
| M6 | top 100 | TSMOM | as M3 | 1.0x |

Each variant runs at the four account sizes, plus two diagnostic cost settings that are not
Scalable's: 0 cost, and 6 bps half-spread with no order fee (the limit for a very large account
or a flat-rate plan).

## Success bar

A variant **passes** only if, at the EUR 25k account size, in BOTH periods it:

1. beats SPY's CAGR by 3 points a year or more,
2. has a Sharpe ratio at least equal to SPY's, and
3. has a max drawdown no worse than SPY's.

Results are also shown next to the equal-weight megacap basket, so it is visible whether the
reversal adds anything beyond holding the same stocks.

Expectation stated in advance: the fixed EUR 1.99 fee is the main risk. With 4 cohorts of 10
names, each week sells about 10 and buys about 10 names, about 1,000 orders a year, or about
EUR 2,000 a year, which is 8% of a EUR 25k account. The top-50 version halves that. Small
accounts are therefore likely to fail on fees alone.
