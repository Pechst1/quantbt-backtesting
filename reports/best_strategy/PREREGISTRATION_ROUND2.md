# Round 2: pushing the Cook panic signal for return. Fixed before any round-2 run

Written and committed on 2026-11-10, before any backtest below was run. Round 1
([PREREGISTRATION.md](PREREGISTRATION.md), [README.md](README.md)) is unchanged and stays reported.

## What is already known, and what that means for this round

Known from round 1 and its audit: R1 (SPY, 1.5x while Cook's breadth state is on) made 15.8% a
year in 2010-2026; the same at 2x made 17.6% and at 3x 21.1%, with worst drawdowns of -67% and
-82% since 1999. The three episodes that hurt were 2002, 2008 and 2020, when SPY fell 20-23%
after the signal came on. Plain momentum (P1) trails SPY in 2010-2019.

So this round is **not a clean test** of "does more leverage in panics pay": I have seen that it
does on SPY. What has not been seen, and is tested here:

1. Whether the signal also separates good from bad days on indices no thread has used with it.
2. Whether a loss stop on the extra leverage (the Market Wizards' first rule) removes the
   2002/2008/2020 damage without giving up the gain.
3. Whether momentum plus panic leverage is better than either alone (momentum is known to do
   worst in the rebound after a panic, which is when the signal is on).

## Part 1. Does the signal generalise? (decides how much weight the rest deserves)

For QQQ, IWM, MDY, EFA and EEM (Yahoo, total return), compare the open-to-open return on days
when the S&P 500 Cook state was on at the previous close with all other days, per window
(1999-2009 where data exists, 2010-2019, 2020-2026-09) and pooled, with a t-statistic.
The Cook state is the one used in round 1, unchanged, computed from S&P 500 members only.

**Pass:** the on-minus-off difference is positive for at least 4 of the 5 indices in 2010-2026,
and the pooled t-statistic across the five (average of the five daily return series, 2010-2026)
is above 2. If Part 1 fails, the report says the signal is likely specific to the S&P 500 sample
and no round-2 variant is recommended over round 1's R1.

## Part 2. Variants

Same simulator, timing, costs and financing as round 1 ("Scalable" headline: fee 0.8 bps of
equity per order; half-spread 6 bps for index ETFs and the 50 largest members, 25 bps otherwise;
borrowing at T-bills + 3%; decisions on the close, fills at the next open; scored from
1999-01-04, one continuous run from 1998-01-02). QQQ comes from Yahoo.

**Loss stop.** While the extra leverage is on, if the index closes more than 7% below its close
on the day the panic state began (O'Neil's 7-8% loss limit), the position goes back to 1x at the
next open and stays there until that panic state has ended.

| | Normal times | While the Cook state is on |
|---|---|---|
| L1 | SPY 1x | SPY 2x |
| L2 | SPY 1x | SPY 2x, with the loss stop |
| L3 | SPY 1x | SPY 3x, with the loss stop |
| Q1 | QQQ 1x | QQQ 1.5x |
| Q2 | QQQ 1x | QQQ 2x, with the loss stop |
| M1 | Momentum book P1 (round 1, unchanged) | 1.5x SPY instead of the momentum book |
| M2 | Momentum book P1 | 2x SPY instead, with the loss stop (stopped: 1x SPY) |

Benchmarks: SPY and QQQ buy and hold; for each variant, the same underlying at constant leverage
equal to the variant's average exposure (financed the same way), to separate timing from leverage.

## Selection rule

A variant **qualifies** if:
1. its CAGR beats its own underlying index (SPY for L and M, QQQ for Q) in 2010-2019 and in 2020-2026;
2. its 2010-2026 CAGR beats the constant-leverage benchmark with the same average exposure;
3. its worst drawdown over 1999-2026 is no deeper than -65%. (Round 1's -50% limit was stricter
   than SPY itself, -55%. This limit is set knowing R1 had -59% and L1 -67%.)

**Winner: the qualifying variant with the highest 2010-2026 CAGR**, provided Part 1 passes.
Separately, and labelled as such, the report names the highest-CAGR variant that meets rules 1
and 2 but not the drawdown limit, for someone who accepts deeper drawdowns.

Robustness for the winner only, not selectable: stops of 5% and 10%; the leverage applied one
day later; "US broker" and zero costs; borrowing at T-bills + 1%; and the days after 2026-09-30.

---

# Part 3 (added 2026-11-10, after Parts 1 and 2 were run and before any Part 3 run)

Parts 1 and 2 are done and reported as they came out: Part 1 failed (pooled t = 1.27), so no
round-2 variant is recommended over R1. Everything the project has tested so far used 1999-2026.
Part 3 asks the remaining return question on data no thread has touched: **how much leverage on
the index, switched by which simple rule, gave the highest long-run growth over 1928-1998**, with
1999-2026 as the already-seen check. This is Ed Thorp's Kelly question (how big to bet) and Paul
Tudor Jones' 200-day rule, both from the books. Earlier results that shaped the choice of rules:
PR #7 found 3x SPY with a 200-day filter unreliable on 1999-2026 and sensitive to the filter
length; round 1 found volatility scaling unhelpful on a momentum book.

## Data

- S&P 500 (S&P 90 before 1957) daily closes from Yahoo `^GSPC`, 1927-12-30 onward.
- Dividends: Shiller's monthly dividend series (github.com/datasets/s-and-p-500), added as
  dividend / price / 252 per day, last known yield carried forward.
- Cash rate: FRED `TB3MS` (previous month's value) from 1934; before that Shiller's long
  government yield as a stand-in.

## Mechanics

- Daily-reset leverage L on the total-return index, like a leveraged ETF.
- A rule's reading on the close of day t sets the position that earns the return of day t+2
  (one full day of delay, since there are no opening prices).
- Borrowed money (L - 1) pays the cash rate + 1.5% a year (a leveraged ETF's swap spread plus
  fee); money not invested earns the cash rate. Each change in L costs 5 bps of the amount traded.

## Variants

| | Rule |
|---|---|
| K1.5, K2, K3 | Constant 1.5x, 2x, 3x |
| T1, T2, T3 | 1x, 2x, 3x while the index closes above its 200-day average; T-bills otherwise |
| TB2 | 2x above the 200-day average; 1x below |
| V2 | Volatility-managed: L = 20% / 21-day realised volatility, capped at 2x; traded when L moves by more than 0.1 |
| V3 | As V2 with a 30% target, capped at 3x |
| TV3 | Above the 200-day average: 30% / 21-day volatility capped at 3x; below: T-bills |

Benchmark: 1x total return.

## Windows and selection

Windows: 1928-1949, 1950-1974, 1975-1998 (all unseen), 1999-2026-09 (seen).

A variant **qualifies** if its CAGR beats the 1x benchmark in at least 3 of the 4 windows,
including at least 2 of the 3 unseen ones, and its worst drawdown over 1928-2026 is no deeper
than the benchmark's own.

**Winner: the qualifying variant with the highest 1928-2026 CAGR.** If none qualifies, the report
says so. Robustness for the winner only, not selectable: averages of 100, 150 and 250 days;
borrowing at cash + 3%; 20 bps per change; two days of delay.
