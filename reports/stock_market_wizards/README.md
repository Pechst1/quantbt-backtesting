# Stock Market Wizards: Okumus, Cook and Shaw/Cohen, tested on S&P 500 members

Run date: 2026-10-02. Rules and all nine variants were committed in
[PREREGISTRATION.md](PREREGISTRATION.md) before the first run; every variant is reported.
One continuous run per variant to 2026-09-30, $100k start, engine with the PR #1 fill fixes,
default costs, cash earns nothing, margin 5%/yr. Sharpe uses rf = 0.

## Results

| | 1999–2019 CAGR | Sharpe | Max DD | 2020–2026 CAGR | Sharpe | Max DD | Avg exposure |
|---|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | -55% | 15.1% | 0.80 | -34% | 1.00 |
| Equal-weight panel members | 10.8% | 0.61 | -57% | 11.9% | 0.64 | -40% | 1.00 |
| **H1-A Okumus, down ≥60%, 10 slots (headline)** | 4.7% | 0.30 | -70% | 5.4% | 0.35 | -41% | 0.55 |
| H1-B Okumus, down ≥50% | 2.4% | 0.23 | -74% | 13.6% | 0.62 | -31% | 0.72 |
| H1-C Okumus, down ≥60%, 20 slots | 5.3% | 0.35 | -64% | 10.1% | 0.51 | -44% | 0.44 |
| **H2-A Cook breadth, cash otherwise (headline)** | 3.6% | 0.38 | -25% | 4.7% | 0.49 | -20% | 0.11 |
| H2-B Cook breadth, SPY otherwise, 2x on signal | 9.4% | 0.46 | -66% | 19.4% | 0.76 | -48% | 1.10 |
| H2-C Cook breadth, SPY otherwise, cash on overbought | 6.2% | 0.44 | -52% | 15.6% | 0.88 | -33% | 0.75 |
| **H3-A Weekly reversal, long bottom decile (headline)** | 7.5% | 0.40 | -66% | 12.2% | 0.55 | -55% | 0.98 |
| H3-B Weekly reversal, long/short deciles (vector, 10 bps/side) | -2.0% | -0.12 | -50% | -1.4% | -0.04 | -34% | 1.00 gross |
| H3-C Monthly reversal, long bottom decile | 6.5% | 0.37 | -78% | 5.2% | 0.32 | -49% | 0.94 |

Pre-registered pass bar: beat SPY's CAGR in both periods with no worse max drawdown.
**No variant passes.** One result is worth following up (H2, below).

## Reading

**H1 Okumus deep value fails.** Buying index members that are 60% off their 1-year high and
selling at +50% or after two years trails SPY in both periods and draws down 64–74%. Half the
trades end on the two-year time stop rather than the target (H1-A: 82 target, 60 time stop).
The in-sample numbers are *flattered*: the panel lacks 309 of 824 members from 1999–2009,
largely bankruptcies, which are exactly the stocks this rule buys. The hold-out (only 1
member missing) is the clean test, and there only the looser 50% variant gets near SPY.
Without Okumus' fundamental filter (P/E < 12, insider buying, balance sheet), "down a lot"
alone is not an edge.

**H2 Cook cumulative breadth: the signal is real, the strategy does not clear the bar.**
- The buy signal fired 43 times since 1998 (about 1.5 a year) and held a median 25 calendar
  days. 35 of 42 closed trades made money, +2.7% on average per trade after costs. In the
  hold-out, 10 of 11 trades won.
- Against random SPY holding periods of the same lengths (20,000 draws,
  `H2_signal_bootstrap.json`): the signal windows average +3.3% vs +0.8% random, 88% vs 64%
  positive; p ≈ 0.0003 for both.
- SPY earned about 39% annualized on signal days in both periods vs 4–13% on other days.
- As a stand-alone strategy (H2-A) it is in the market only 11% of the time, so it makes
  3.6–4.7% a year with a -20 to -25% worst drawdown.
- Used as a leverage switch (H2-B: SPY normally, 2x on signals) it beats SPY by +2.8%/yr
  in-sample and +4.3%/yr in the hold-out, but its worst drawdown is deeper (-66% vs -55%,
  -48% vs -34%), because signals also fire inside crashes (Oct 2008 -7%, Mar 2020 at 2x).
  Sharpe is level with SPY, so most of the extra return is extra risk. It fails the
  pre-registered drawdown bar.
- The overbought "sell" side (H2-C) adds nothing in-sample and slightly improves Sharpe in
  the hold-out (0.88 vs 0.80); not an edge.
- Caveat: daily breadth of S&P 500 members is a proxy for Cook's intraday NYSE tick, and the
  21-day sum window is my default (the book gives none).

**H3 short-term reversal fails, as expected beforehand.** The weekly long-only loser book
beats SPY in-sample (7.5% vs 6.6%) but trails the equal-weight panel (10.8%), so it adds
nothing over simply holding all members; in the hold-out it trails SPY by 3%/yr. It trades
about 2,000 names a year. The market-neutral version (H3-B) loses about 2% a year after
10 bps per side at roughly 89x annual turnover: whatever reversal premium is left in large
caps is smaller than retail trading costs. Shaw's edge depended on execution and data we
cannot replicate with daily bars.

## What this suggests next (not run, would need its own pre-registration)

The only candidate with out-of-sample evidence is Cook's breadth signal used as a timing
overlay. A natural next test is to size the overlay so it does not raise drawdown, for
example 1.5x instead of 2x, or adding exposure only when SPY is above its 200-day average.
Both would be new rules and must be fixed before any run.

## Caveats

- Survivorship: see H1 above. It matters least for H2 (index-level breadth) and the hold-out.
- Held names that stop trading keep their last price (1–2 positions per H1 variant).
- H3-B ignores borrow fees and short rebates, which would make it worse.
- H2 runs start their warm-up in 1996 (first signal 1998-04) because the percentiles need two
  years of history; this is a warm-up change, not a parameter change.

## Files and reproduction

`H*_summary.json` (metrics by period, calendar years, trade stats), `H*_equity.csv`,
`H*_trades.csv`, `H2-*_breadth.csv` (daily breadth, running sum and state),
`benchmarks_*`, `H2_signal_bootstrap.json`.

    python examples/fetch_sp500_panel.py
    python examples/run_stock_market_wizards.py --benchmarks
    python examples/run_stock_market_wizards.py --variant H1-A   # ... H3-C
    python examples/cook_signal_bootstrap.py

Sources for the methods: Schwager, *Stock Market Wizards* (2001), chapters on Ahmet Okumus,
Mark D. Cook, David Shaw and Steve Cohen; Lehmann (1990); Jegadeesh (1990); De Bondt &
Thaler (1985).
