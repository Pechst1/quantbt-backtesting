# Market Wizards: The Next Generation (Schwager & Coyle, June 2026): Kullamägi's setups

Rules and all seven variants were pre-registered in [PREREGISTRATION.md](PREREGISTRATION.md)
and committed before the first run. Nothing was tuned afterwards.

## Result

**None of the seven variants passes. Kristjan Kullamägi's breakout and episodic pivot (EP)
setups, as written in public, trail SPY in every period and universe tested.**

* **S&P 500 members, 1999-2026:** 1-3% CAGR in-sample and 2-5% in the hold-out, against SPY's
  6.2% and 15.1%. The setups rarely fire in large caps, so the book is only 2-7% invested and
  returns are mostly T-bill interest. Per trade there is a small positive edge in some cells
  (P&L per dollar traded: EP +0.8% in-sample, breakout +1.9-2.3% in the hold-out), but it does
  not repeat across periods. EP is +0.8% in-sample and about 0 in the hold-out. The breakout is
  about 0 in-sample and positive only after 2020, on 62 trades.
* **All US common stocks, 2022-04 to 2026-09 (his actual hunting ground, small and mid caps,
  delisted names included):** the breakout loses 8.2% a year and the EP loses 19.9% a year,
  against SPY's +12.3%. Both lose about 1% per dollar traded after costs. The famous 5-20R
  winners did not show up often enough to pay for a 33-37% win rate. The largest single
  breakout winner made about 4% of starting equity, while overnight gaps through stops
  regularly cost 1.5-2.5R.

| Variant | Period | CAGR | Sharpe | Max DD | SPY CAGR | SPY Sharpe | SPY DD | Avg invested | Fills closed | Win rate | Avg trade |
|---|---|---|---|---|---|---|---|---|---|---|---|
| KB-A10 | in_sample_1999_2019 | 1.8% | 0.04 | -13% | 6.2% | 0.32 | -55% | 2% | 218 | 39% | +1.44% |
| KB-A10 | holdout_2020_2026 | 4.8% | 0.46 | -6% | 15.1% | 0.66 | -34% | 2% | 62 | 40% | +3.54% |
| KB-A20 | in_sample_1999_2019 | 1.5% | -0.03 | -14% | 6.2% | 0.32 | -55% | 3% | 224 | 34% | +1.16% |
| KB-A20 | holdout_2020_2026 | 5.1% | 0.43 | -6% | 15.1% | 0.66 | -34% | 3% | 62 | 39% | +4.21% |
| KB-C10 | in_sample_1999_2019 | 1.1% | -0.10 | -26% | 6.2% | 0.32 | -55% | 7% | 564 | 36% | +0.90% |
| KB-C10 | holdout_2020_2026 | 3.0% | 0.05 | -7% | 15.1% | 0.66 | -34% | 6% | 174 | 33% | +1.55% |
| EP-10 | in_sample_1999_2019 | 3.1% | 0.36 | -7% | 6.2% | 0.32 | -55% | 4% | 312 | 48% | +1.64% |
| EP-10 | holdout_2020_2026 | 1.8% | -0.16 | -13% | 15.1% | 0.66 | -34% | 5% | 134 | 36% | +0.92% |
| EP-20 | in_sample_1999_2019 | 2.9% | 0.28 | -7% | 6.2% | 0.32 | -55% | 6% | 307 | 43% | +1.62% |
| EP-20 | holdout_2020_2026 | 2.0% | -0.11 | -12% | 15.1% | 0.66 | -34% | 7% | 134 | 31% | +1.02% |
| US-KB-A10 | us_2022_2026 | -8.2% | -0.85 | -38% | 12.3% | 0.52 | -22% | 26% | 668 | 33% | +1.61% |
| US-EP-10 | us_2022_2026 | -19.9% | -1.21 | -68% | 12.3% | 0.52 | -22% | 82% | 1222 | 36% | +1.16% |

"Avg trade" is the unweighted mean return of each closed fill, partial sales included, so it
flatters. The P&L-weighted figures quoted above are the honest per-trade edge.

## Why it probably fails here

* Daily bars can't reproduce his entries. He buys the opening-range high intraday and stops at
  the low of the day. Here a breakout fills at the pivot as a stop order, and an EP is bought
  at the next day's open after a gap that held into the close. By then much of the day-1 move is gone.
* His edge is selective and discretionary: catalyst quality, how "tight" a base looks,
  sector themes, and sizing up into his best ideas. None of that is in public rules.
* Costs: 9.5 bps per side is cheap for the small caps in the US test, so real results would be worse.

## Fixes made during the run (code, not parameters)

* Stocks that stopped trading (delistings) used to count as spendable cash. In the first
  all-US EP run that drove cash negative and caused margin calls (first pass: -39% CAGR).
  Fixed in `KullamaggieStrategy`, with a regression test, and all seven variants re-run.
  S&P results moved by at most 0.4 points.
* US runs use their own data cache, so S&P panel files are never reused for shared tickers.

## Caveats

* The S&P panel misses about a third of historical members (bias +1-2%/yr, PR #4).
* Massive grouped bars are split-adjusted but not dividend-adjusted. Tickers that were reused
  by a different company are not separated (2 suspicious EP entries out of about 1,400).
* A position whose stock delists is held at its last close, because the engine can't sell into a missing bar.
* The engine can't stop out on the entry bar itself, which is slightly optimistic.

## Other traders in the book

Lance Breitstein (intraday capitulation reversals), Kelvin Chiu (agricultural options), KQ,
Phil Goedeker and Lukas Frohlich have no published mechanical rules. Daily-bar cousins of
Breitstein's idea (RSI(2), Cook breadth, reversal) were already tested in PRs #10 and #11.

## Reproduce

```
python examples/fetch_sp500_panel.py
MASSIVE_API_KEY=... python examples/build_massive_us_panel.py
python examples/run_kullamaggie.py            # all seven variants
```
