# Best-strategy search: vol-scaled momentum, panic-only reversal, trend sleeve

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
