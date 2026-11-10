# Best-strategy search: vol-scaled momentum, panic-only reversal, trend sleeve

> **Round 2 (2026-11-10) is at the end of this file.** It changes the recommendation for someone
> who wants the highest return: a trend-and-volatility leverage rule on the S&P 500, validated on
> 1928-1998, with the Cook panic signal as an override. Round 1 below is unchanged.

Run date: 2026-11-09, data through 2026-11-05. Rules, the 11 variants, costs and the selection
rule were committed in [PREREGISTRATION.md](PREREGISTRATION.md) before the first run. Every
variant is reported in [results.md](results.md). Reproduce with
`python examples/fetch_sp500_panel.py && python examples/run_best_strategy.py && python examples/audit_best_strategy.py`.

**Bottom line: no variant passes the pre-registered rule, and most of the hypotheses were wrong.**
Volatility scaling, the 52-week-high ranking and the combined levered book all made returns
worse. The one idea that held up in every window and every check is Mark Cook's breadth panic
signal. The strategy I would trade is **R1: hold the S&P 500, and go to 1.5x for the few weeks
a year the signal is on**. It made 15.8% a year over 2010-2026 against 14.1% for SPY, with about
3 orders a year. That is a modest edge, not a highly profitable system.

## Results at Scalable costs (CAGR / Sharpe / worst drawdown)

| | 1999-2009 | 2010-2019 | 2020-2026 | 2010-2026 | Worst DD | Orders/yr | Passes |
|---|---|---|---|---|---|---|---|
| SPY total return | 0.8% / 0.15 / -55% | 13.3% / 0.92 / -19% | 15.1% / 0.80 / -34% | 14.1% / 0.86 | -55% | | |
| P1 momentum top 10, always in | 0.3% / 0.18 / -69% | 11.2% / 0.56 / -38% | 23.8% / 0.76 / -36% | 16.3% / 0.65 | -69% | 70 | no |
| P2 + vol scaling 20%, cap 1x | 5.8% / 0.38 / -44% | 9.1% / 0.55 / -30% | 11.9% / 0.61 / -30% | 10.3% / 0.58 | -44% | 104 | no |
| P3 + vol scaling 30%, cap 2x | 4.3% / 0.29 / -62% | 10.0% / 0.46 / -43% | 12.1% / 0.50 / -44% | 11.1% / 0.49 | -62% | 133 | no |
| P4 as P2, 52-week-high ranking | 0.5% / 0.12 / -42% | 6.8% / 0.53 / -21% | 1.0% / 0.15 / -33% | 4.4% / 0.35 | -42% | 194 | no |
| P5 + buffered 200-day rule | 13.3% / 0.63 / -35% | 9.2% / 0.54 / -26% | 8.0% / 0.40 / -38% | 8.9% / 0.46 | -38% | 58 | no |
| **R1 SPY, 1.5x during Cook panic** | 2.2% / 0.22 / -59% | **15.3%** / 0.93 / -22% | **16.6%** / 0.74 / -44% | **15.8%** / 0.82 | -59% | 3 | no (drawdown) |
| R2 SPY, megacap losers during panic | 7.8% / 0.39 / -65% | 17.0% / 1.00 / -23% | 13.9% / 0.65 / -40% | 15.8% / 0.82 | -65% | 64 | no |
| R3 as R2, Cook-or-VIX trigger | 13.6% / 0.51 / -60% | 13.9% / 0.78 / -36% | 19.5% / 0.75 / -40% | 16.2% / 0.75 | -60% | 172 | no (drawdown) |
| C1 P2 + idle capital into panic losers | 10.5% / 0.50 / -61% | 11.7% / 0.64 / -31% | 15.3% / 0.67 / -37% | 13.2% / 0.65 | -61% | 160 | no |
| T1 trend sleeve alone (2008+) | | -0.1% / 0.04 / -26% | 1.7% / 0.21 / -19% | 0.7% / 0.12 | -32% | | no |
| C2 70% C1 + 30% trend, levered (2008+) | | 6.7% / 0.41 / -33% | 7.2% / 0.42 / -30% | 7.1% / 0.42 | -40% | | no |

By the written rule the closest variant is R3 (it fails only the -50% drawdown limit). The
drawdown limit was stricter than SPY itself (-55%), so every SPY-based variant fails it in 2008.
I did not relax the rule; the recommendation below is my judgment and is labelled as such.

## What the hypotheses turned out to be

- **Volatility scaling on momentum: wrong.** It cut the worst drawdown from -69% to -44%, but
  the Sharpe did not improve (0.56 to 0.55 in 2010-2019, 0.76 to 0.61 since 2020). The leaders
  of a strong market are volatile, so scaling to 20% kept the book about 75% invested during
  exactly the runs that make the money. With leverage (P3) it was worse again.
- **52-week-high ranking: wrong.** 1.0% a year since 2020. Large caps sitting near their highs
  are mostly quiet stocks, not leaders, and the book turned over three times as fast.
- **Buffered 200-day rule: same failure as before.** It helps 2000-2002 and 2008 and loses in
  both later windows.
- **Combined levered book: wrong.** The long-or-flat ETF trend sleeve earned about nothing after
  costs and financing, so it diluted the book instead of diversifying it.
- **Plain momentum (P1) is one regime.** It trails SPY in 2010-2019 at 5, 10 and 20 names and on
  a mid-month rebalance; its lead comes from 2020-2026 and above all 2026 (+87% to September).
  In the five weeks after 2026-09-30, which no earlier thread had seen, it lost 24% while SPY
  lost 1%. The book was ten memory, storage and chip-equipment stocks.
- **Panic stock-picking (R2, R3) is fragile.** With 10 losers instead of 5, R3 falls to 12.2%
  in 2010-2019 and trails SPY. It is cost-sensitive (21.0% at zero cost, 16.2% at Scalable),
  and its 1999-2009 numbers are flattered: the panel lacks Lehman, WaMu and similar names, which
  are exactly what "buy the biggest losers in a panic" would have bought. In the unseen five
  weeks R2 and R3 lost 13%.

## What held up: the Cook panic signal

SPY's return while the signal is on, measured open to open from the day after the signal (as
traded), against the rest of the time ([audit.json](audit.json)):

| | Days on | SPY annualised, on | SPY annualised, off | t-stat of the difference |
|---|---|---|---|---|
| 1999-2009 | 15% | +34% | -2% | 1.2 |
| 2010-2019 | 8% | +57% | +10% | 1.7 |
| 2020-2026 | 12% | +36% | +14% | 0.6 |

- The direction is the same in all three windows. No single window is statistically
  significant on its own, so treat this as a tilt with a sound reason behind it (returns are
  higher after forced selling), not a proven edge.
- 35 of the 42 episodes since 1999 ended with SPY higher. The losers were the ones that matter
  for leverage: June-August 2002 (-5%, -20% at the worst point), October-December 2008 (-7%,
  -23%), March-April 2020 (-5%, -23%).
- R1 beats constant leverage with the same average exposure: 15.8% against 14.5% for a
  constant 1.06x SPY over 2010-2026, and 10.3% against 8.8% over 1999-2026. So the gain is the
  timing, not just the extra exposure.
- An independent check without the simulator gives 15.9% for 2010-2026.
- R1 only ever holds SPY, so missing delisted stocks cannot flatter it. Costs barely matter
  (15.8% at Scalable, 16.0% at zero cost), and three orders a year keep taxes deferred.

The leverage while the signal is on is a risk dial, not a new strategy (Scalable costs):

| Leverage during panic | 2010-2019 | 2020-2026 | 2010-2026 | Worst DD 2010-2026 | Worst DD 1999-2026 |
|---|---|---|---|---|---|
| 1.0x (SPY) | 13.3% | 15.1% | 14.1% | -34% | -55% |
| 1.25x | 14.3% | 15.8% | 14.9% | -39% | -57% |
| **1.5x (R1)** | 15.3% | 16.6% | 15.8% | -44% | -59% |
| 2.0x | 17.3% | 18.0% | 17.6% | -54% | -67% |
| 3.0x | 21.3% | 20.8% | 21.1% | -75% | -82% |

Return rises about 1.7 points for each extra 0.5x, and the drawdown deepens by about 10 points.
The Sharpe falls as leverage rises (0.82 at 1.5x, 0.67 at 3x), so the dial buys return with risk.

## How R1 would be traded

- Hold an S&P 500 ETF. On the close when the Cook state turns on, move half the position into
  a 2x daily S&P 500 ETF at the next open (50% 1x + 50% 2x = 1.5x). Move back when it turns off.
- The state is in `cook_state.csv` (column `buy`); it was last on from 2026-09-23 to 2026-10-20
  and is off as of 2026-11-05. It needs the day's advancers and decliners among S&P 500 members.
- The backtest borrows at T-bills + 3% instead of using a 2x ETF. A 2x ETF's daily reset adds
  some path dependence over a 2-8 week episode, which is not modelled.

## Caveats

- 1999-2009 uses a panel that misses about 40% of members in 1999 and 10% in 2008.
- 2020-2026 had been seen by earlier threads. The only truly unseen data is 2026-10-01 to
  2026-11-05, which is five weeks.
- The EUR/USD exchange rate and taxes are ignored.
- C2 is combined from daily return series, not simulated order by order.
- After this report, 40-odd variants have been tried on this data across the project. A 1.7
  point edge with a t-statistic below 2 in each window could still be luck.

---

# Round 2 (2026-11-10): pushing for return

Rules were fixed before each part was run, in
[PREREGISTRATION_ROUND2.md](PREREGISTRATION_ROUND2.md). Generated tables: [round2.md](round2.md),
[century.md](century.md). Reproduce with `python examples/run_best_strategy_round2.py`,
`python examples/run_century_leverage.py` and `python examples/run_century_part4.py`.

**Bottom line.** The highest-returning strategy the evidence supports is a three-state rule on
the S&P 500, called TC below:

1. **Index above its 200-day average:** hold it with leverage of 30% divided by the last
   21 days' annualised volatility, capped at 3x (about 2.4x on average in those periods).
2. **Index below its 200-day average:** T-bills.
3. **Cook panic state on:** at least 1.5x, whatever the first two say.

In the Part 3 framework it made 15.6% a year over 1999-2026 against 8.7% for the index, and
20.5% against 14.2% over 2010-2026, with a worst drawdown of -50% against -55%. Its worst
calendar year was -34% (2015). The first two states were validated on 1928-1998, which no earlier
test had used. The third state and the combination rest on 1999-2026 only, which had been seen.

## Part 1: the Cook signal on other indices. Fails the bar

Return on days after the S&P 500 Cook state was on, against other days, 2010-2026:

| | On, annualised | Off, annualised | t-stat |
|---|---|---|---|
| QQQ | 53.5% | 16.1% | 1.51 |
| IWM | 38.1% | 10.0% | 1.06 |
| MDY | 42.8% | 9.6% | 1.32 |
| EFA | 30.6% | 5.9% | 1.07 |
| EEM | 29.9% | 4.7% | 0.97 |
| Average of the five | 39.0% | 9.2% | 1.27 |

The difference is positive on all five, but the pooled t-statistic is 1.27, below the 2.0 I
required. Almost all of it comes from 2010-2019; in 2020-2026 the pooled difference is close to
zero (t = 0.14). So the panic signal is a plausible tilt, not an established edge.

## Part 2: more leverage in panics, a loss stop, momentum plus panic. Nothing recommended

| Scalable costs | 2010-2019 | 2020-2026 | 2010-2026 | Worst DD 1999-2026 | Constant leverage, same exposure |
|---|---|---|---|---|---|
| SPY | 13.3% | 15.1% | 14.1% | -55% | |
| L1 SPY; 2x in panic | 17.3% | 18.0% | 17.6% | -67% | 15.0% |
| L2 SPY; 2x in panic, 7% stop | 16.4% | 17.5% | 16.9% | -65% | 14.9% |
| L3 SPY; 3x in panic, 7% stop | 19.3% | 19.7% | 19.5% | -73% | 15.6% |
| QQQ | 17.7% | 20.7% | 19.0% | -83% | |
| Q1 QQQ; 1.5x in panic | 19.9% | 23.0% | 21.2% | -82% | 19.7% |
| Q2 QQQ; 2x in panic, 7% stop | 21.1% | 22.8% | 21.9% | -86% | 20.0% |
| M1 momentum; 1.5x SPY in panic | 8.5% | 16.1% | 11.6% | -68% | 14.6% |
| M2 momentum; 2x SPY in panic, 7% stop | 9.6% | 17.3% | 12.8% | -70% | 14.9% |

- Only L2 meets the three rules, and because Part 1 failed, the rule says not to recommend it
  over R1.
- **The loss stop did not help.** It cost 0.7 points a year and improved the worst drawdown by
  two points. The damage in 2002, 2008 and 2020 came faster than a 7% stop could act on.
- **Momentum plus panic leverage is worse than either part.** Swapping ten stocks for the index
  and back a few times a year costs more than it gains.
- The QQQ versions return more only because QQQ did; their timing gain is 1.5-2 points, and
  QQQ lost 83% in 2000-2002.

## Part 3: leverage rules on the S&P 500 since 1928. A winner on unseen data

Daily-reset leverage on the index with dividends, borrowing at the cash rate + 1.5%, 5 bps per
change, and one full day between a reading and the position it sets. CAGR / worst drawdown:

| | 1928-1949 | 1950-1974 | 1975-1998 | 1999-2026 | 1928-2026 | Qualifies |
|---|---|---|---|---|---|---|
| 1x | 5.3% / -84% | 10.0% / -45% | 16.9% / -33% | 8.7% / -55% | 10.2% / -84% | |
| Constant 2x | 0.2% / -99% | 13.5% / -76% | 22.7% / -61% | 10.0% / -88% | 11.6% / -99% | no |
| Constant 3x | -11.5% / -100% | 15.5% / -90% | 25.5% / -80% | 7.2% / -98% | 8.8% / -100% | no |
| T2: 2x above the 200-day, else T-bills | 10.0% / -80% | 19.1% / -29% | 19.6% / -33% | 10.2% / -44% | 14.6% / -80% | yes |
| T3: 3x above the 200-day, else T-bills | 9.5% / -93% | 25.9% / -41% | 23.2% / -48% | 11.7% / -63% | 17.3% / -93% | no |
| V2: volatility-managed, cap 2x | 6.4% / -68% | 13.5% / -55% | 19.0% / -40% | 9.2% / -60% | 12.0% / -68% | yes |
| **TV3: above the 200-day, 30% / volatility, cap 3x** | 10.2% / -62% | 23.5% / -39% | 19.3% / -39% | 10.0% / -43% | **15.5% / -62%** | **yes** |

- **Constant leverage does not pay.** Over 98 years 2x added 1.4 points a year for a -99%
  drawdown, and 3x ended below the unlevered index. This is Thorp's point about betting too big.
- **Leverage only in uptrends and only when markets are calm does pay.** TV3 beat the index in
  all four windows, including the three that no test in this project had touched, with a
  smaller worst drawdown than the index itself.
- **The edge is much thinner in the modern period.** Over 2010-2026 TV3 made 14.6% against
  14.2% for the index, with a lower Sharpe (0.63 against 0.86). Recoveries since 2009 have been
  fast, and the rule buys back late.
- **It is fragile to its settings.** With a 100-day average it made 11.7% over the full period
  and 5.8% in 1999-2026 (below the index). At 20 bps per change it made 12.4% and 6.9%. The
  150- and 250-day versions made 15.0% and 14.1%. It needs cheap trading: about 30 small
  adjustments a year.

## Part 4 (exploratory, seen data): TV3 with the Cook override

TV3 is in T-bills on most panic days, and round 1 found those days were the best ones for SPY.
One combined variant, declared before it was run, on 1999-2026:

| Part 3 framework | 1999-2009 | 2010-2019 | 2020-2026 | 2010-2026 | 1999-2026 | Worst DD |
|---|---|---|---|---|---|---|
| 1x | 0.9% | 13.3% | 15.3% | 14.2% | 8.7% | -55% |
| R1: 1x; 1.5x in panic | 1.8% | 15.4% | 16.6% | 16.0% | 10.2% | -60% |
| TV3 | 3.0% | 13.6% | 15.6% | 14.6% | 10.0% | -43% |
| **TC: TV3, at least 1.5x in panic** | 8.0% | 19.6% | 21.4% | **20.5%** | **15.6%** | -50% |

- TC spends 19% of days in T-bills and 53% above 2x; average leverage is 1.9x.
- Calendar years swing widely: +81% in 2013, +67% in 2017, -34% in 2015, -16% in 2008, +3% in
  2020, -3% in 2022.
- Descriptive checks, 1999-2026 CAGR: 150-day average 14.2%; 250-day 14.5%; two days of delay
  15.2%; borrowing at cash + 3% 13.7%; 20 bps per change 12.2%; panic floor 1.0x 14.0%; panic
  floor 2.0x 16.6%; a tamer version capped at 2x with a 20% volatility target 13.8% (worst
  drawdown -46%). All stay well above the index's 8.7%.
- **Why this is weaker evidence than Part 3:** both ingredients were chosen after seeing
  1999-2026, the Cook leg failed Part 1's significance bar, and the combination gains most of its
  lead from holding 1.5x instead of T-bills on panic days, which triples the size of the bet that
  R1 makes. If the panic effect was luck, TC falls back to roughly TV3, which only matched the
  index in 2010-2026.

## Recommendation after round 2

- **For the highest return: TC.** Expect it to be worth less than the table: the honest range is
  from "about the index" (if the panic effect does not persist) to several points a year above it.
- **For a smaller, simpler bet: R1** from round 1 (index, 1.5x in panics, 3 orders a year).
- **How TC would be traded:** an S&P 500 ETF, a 2x and a 3x daily S&P 500 product, and a
  money-market ETF, mixed to the wanted leverage; reviewed after each close and changed when the
  wanted leverage moves by more than 0.1. Daily-reset products match the backtest's mechanics.
- **State on 2026-11-10:** the index is above its 200-day average, 21-day volatility is 21%, so
  the wanted leverage is 1.4x. The Cook state is off (last on 2026-09-23 to 2026-10-20).
- **Not modelled:** the EUR/USD rate, taxes on about 36 adjustments a year, tracking error and
  fees of leveraged products beyond the 1.5% financing spread, and the fixed order fee (small for
  accounts of EUR 25k and up, since each change is one or two orders).
- Before real money: paper-trade TC next to the index for at least six months.
