# Megacap reversal + Cook breadth + trend filter, at Scalable Capital costs

Run date: 2026-10-03. Rules, six variants, account sizes and the success bar were committed in
[PREREGISTRATION.md](PREREGISTRATION.md) before the first run. Every variant is reported.
Reproduce with `python examples/fetch_sp500_panel.py && python examples/run_megacap_reversal.py
&& python examples/audit_megacap_reversal.py`. Full tables: [results.md](results.md),
[audit.md](audit.md).

**Bottom line: three variants (M3, M5, and M3/M5 at larger accounts) pass the pre-registered
bar on paper, but the audit says the in-sample win is mostly a data artefact, so I would not
trade this as a SPY replacement.** In the near-complete 2010-2019 data, every variant trails
SPY at Scalable costs. The 2020-2026 lead is real in the data, but most of it is the megacap
rally that an equal-weight basket of the same 50 stocks also caught.

## Pre-registered results (EUR 25k account, Scalable costs)

1999-2019 and 2020-2026-09 are separate runs, each starting with EUR 25,000. Costs are EUR 1.99
per order plus 6 bps half-spread. Benchmarks have no costs.

| | 1999-2019 CAGR | Sharpe | Max DD | 2020-2026 CAGR | Sharpe | Max DD | Orders/yr | Pass |
|---|---|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | -55% | 15.1% | 0.80 | -34% | | |
| Equal-weight top 100 | 8.3% | 0.48 | -59% | 16.4% | 0.82 | -34% | | |
| Equal-weight top 50 | 7.3% | 0.42 | -62% | 20.4% | 0.93 | -32% | | |
| M1 reversal, top 100, always in | account wiped out | | -100% | 19.4% | 0.76 | -41% | 660-930 | no |
| M2 + 200-day filter | 0.7% | 0.13 | -41% | 13.3% | 0.73 | -25% | 700-770 | no |
| **M3 + 200-day + Cook (headline)** | **10.4%** | 0.54 | -44% | **19.2%** | 0.83 | -31% | 780-830 | yes |
| M4 as M3, 1.5x on Cook signals | 13.7% | 0.57 | -60% | 22.4% | 0.81 | -40% | 810-890 | no (drawdown) |
| **M5 as M3, top 50** | **13.1%** | 0.62 | -52% | **23.4%** | 0.90 | -30% | 390-410 | yes |
| M6 as M3, 12-month momentum filter | 8.6% | 0.46 | -47% | 17.6% | 0.74 | -41% | 770-830 | no |

Account size matters a lot because of the fixed fee (CAGR 1999-2019 / 2020-2026):

| | EUR 10k | EUR 25k | EUR 50k | EUR 100k | 6 bps, no fee | 0 cost |
|---|---|---|---|---|---|---|
| M3 | wiped out / 7.6% | 10.4% / 19.2% | 11.9% / 21.9% | 12.6% / 23.1% | 13.1% / 24.3% | 14.7% / 26.0% |
| M5 | 11.1% / 18.9% | 13.1% / 23.4% | 13.7% / 24.6% | 13.9% / 25.3% | 14.2% / 25.9% | 15.7% / 27.6% |

- At EUR 10k, the fee costs 14-19% a year for the top-100 books, and M1, M2, M3 and M6 lose the
  whole account in 1999-2019. M5 (half as many orders) survives at about 2-5% a year in fees.
- M3 at EUR 25k trades about 800 orders a year, about 15 a week, and pays 2-4% of equity a
  year in fees plus about 1.4% in spread.

## Audit: why I don't trust the pass

These checks were added after seeing the results. They change no rule. Each period is a fresh
EUR 25k run ([audit.md](audit.md)).

| CAGR | 1999-2009 | 2010-2019 | 2020-2026 |
|---|---|---|---|
| SPY | 0.8% | 13.3% | 15.1% |
| Equal-weight top 100 / top 50 | 4.0% / 1.7% | 13.1% / 13.5% | 16.4% / 20.4% |
| M3 at EUR 25k | 12.0% | **3.1%** | 19.2% |
| M3 at 0 cost | 17.4% | 11.3% | 26.0% |
| M5 at EUR 25k | 14.2% | **9.6%** | 23.4% |
| M5 at 0 cost | 17.3% | 13.7% | 27.6% |

1. **The in-sample win comes from the decade with missing data.** The price panel lacks 212 of
   501 index members in 1999 and 75 in 2008, and the missing ones are exactly the large
   companies that collapsed: Lehman, Washington Mutual, Wachovia, Merrill Lynch, National
   City, Sun, Yahoo and others. "Buy the weakest large stocks" would have bought them on the way
   down. In 2010-2019, where the panel is 90-98% complete, M3 makes 3.1% and M5 9.6% at Scalable
   costs against SPY's 13.3%. Even at zero cost they at best match SPY (11.3% and 13.7%).
2. **The Cook override is the part that works, but it is rare.** On the days M3 is invested only
   because of a Cook signal, it made +169% (1999-2009), +76% (2010-2019) and +35% (2020-2026)
   at zero cost, against +32%, +50% and +18% for SPY on the same days. That matches PR #11: the
   signal is real. But it covers only about 10 to 25 days a year, and it can't rescue the reversal
   core in a calm bull market.
3. **The 2020-2026 lead is mostly megacap exposure.** M5 makes 23.4% against 15.1% for SPY, but
   holding the same 50 stocks equal-weight made 20.4%. The reversal itself adds about 3 points,
   and this period had already been seen by PR #10 and #11, so it is not a clean hold-out.
4. **The trend filter alone is fragile.** Moving the weekly check from Friday to Wednesday
   changes M2's 2020-2026 result from 13.3% to 4.5%. M3 and M5 hardly move (18.5% and 22.6%),
   because the Cook override covers the whipsaws.
5. **Drawdowns are still deep.** M3 fell 44% and M5 52% in 1999-2019, against 55% for SPY. That
   is better than the 71% of the original R4, but it is not crash protection.

## Practical points for a Scalable account

- Fees: below about EUR 25k the fixed EUR 1.99 eats the edge. Only M5 is tolerable at 10k.
- Work: M5 means about 8 orders every week, placed on Monday at the US open, plus extra
  rounds whenever the trend or Cook state flips.
- Taxes (not modelled): every week realizes gains, so the 26.4% German capital gains tax is
  paid each year, while SPY defers it. At 10-20% gains that is roughly 2-4 points a year less.
- Fractional shares are assumed; with whole shares, a EUR 25k book of 20 names is lumpy.

## What this means for the search

This is the best-looking result in the project so far, but it fails the most important check:
in the only clean decade before the hold-out, it trails SPY. My plain read is that a
long-only reversal on large caps does not beat SPY reliably after Scalable's costs. The one
signal that keeps holding up is Cook's breadth buy signal, and it is on only a few weeks a year.

If you still want to try something, the honest version is to paper-trade M5 forward from
today at EUR 25k or more and compare with SPY after a year, rather than trust the backtest.

## Notes and caveats

- Universe: top N by 63-day average dollar volume (no point-in-time market caps in the panel).
- Data fix made before any result was seen: the panel had one stray row on Good Friday 2017,
  which blanked the 63-day dollar-volume windows for 13 weeks and crashed the first run.
  Dates with under half the usual number of names are dropped.
- The Cook state reproduces PR #11's breadth series exactly (873 of 873 signal days).
- Returns are in USD; EUR/USD moves affect SPY equally and are ignored.
- Borrowing for M4 costs T-bills + 3%. M4 also fails on drawdown (-60% and -40%).
