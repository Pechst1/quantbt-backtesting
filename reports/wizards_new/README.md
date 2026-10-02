# Untested Market Wizards methods: Parker, Thorp, and the volatility premium

Run date: 2026-10-02. Rules, variants and the success bar were committed in
[PREREGISTRATION.md](PREREGISTRATION.md) before the first run (one amendment, made after a data
check and before any strategy run, is recorded there). H4, the slower reversal follow-up, was
pre-registered after H1–H3 were reported and before any H4 run. Every variant is reported. Reproduce with
`python examples/fetch_sp500_panel.py && python examples/run_new_wizards.py`.

**Bottom line: none of the 20 variants clears the bar** (beat SPY by 3+ points a year in both
the in-sample period and the 2020–2026 hold-out, with an equal or better Sharpe). Two of the
three ideas have a real raw edge, but trading costs eat it: mean reversion makes 18% a year
before costs from 1999 to 2019, and stat-arb makes 15–18%, yet both fall below SPY once 10 bps
per side is charged. The volatility premium pays well in calm years, but it wiped out 93% of
the account in February 2018. Slowing the reversal down (H4) cuts the costs but loses the
signal faster: the 4-week long/short book makes only 5.6%/5.9% a year even at zero cost. The
best long-only book (R4) beats SPY in both periods (10.6% vs 6.6%, 17.5% vs 15.2%), but by
less than 3 points in the hold-out, with a lower hold-out Sharpe and a −71% drawdown, and
in-sample it only matches the equal-weight basket.

## Results

IS = in-sample (1999–2019 for H1/H3, 2011-11 to 2019 for H2, the SVXY history). HO = hold-out
2020-01-01 to 2026-09-30. Sharpe uses rf = 0. Costs are 10 bps per side, and idle cash earns T-bills.

### H1. Marsten Parker style dip-buying (Connors RSI(2) trigger), S&P 500 members

| Variant | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD |
|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | −55% | 15.2% | 0.81 | −34% |
| Equal-weight members | 10.6% | 0.60 | −58% | 11.9% | 0.64 | −40% |
| A: RSI(2)<5, exit close > SMA5, 10 slots | 8.3% | 0.60 | −32% | 1.7% | 0.18 | −30% |
| B: RSI(2)<5, Parker exit (first up close / 5 days) | 1.1% | 0.15 | −64% | −4.6% | −0.24 | −48% |
| C: RSI(2)<10, exit close > SMA5 | 9.5% | 0.62 | −32% | 3.6% | 0.28 | −33% |
| D: A + SPY above 200-day for entries | 6.8% | 0.60 | −24% | 1.4% | 0.17 | −28% |
| E: A with 20 slots | 8.1% | 0.68 | −29% | 4.9% | 0.42 | −19% |
| A at 20 bps per side | −0.6% | 0.04 | −71% | −6.9% | −0.35 | −47% |
| *Diagnostic: A at 0 bps (not tradable)* | *18.0%* | *1.16* | *−29%* | *11.0%* | *0.72* | *−22%* |

- The dips do bounce: variant A wins 62% of its trades, holds about 4 days, and averages
  +0.16% per trade after costs. But it makes about 430 round trips a year at 10% of equity
  each, so every extra 10 bps per side costs about 9 points of CAGR.
- The edge has faded. A made 33%, 75% and 26% in 1999–2001, but from 2012 onward it mostly
  made between −3% and +11% a year, and it lost 24% in 2018. Connors published the rule in
  2008. In the hold-out, even the zero-cost version (11.0%) trails SPY.
- Parker's own exit (B) is the worst variant. Selling on the first up close cuts the bounce
  short, and the trades average about zero.
- Survivorship bias flatters dip-buying the most, because the panel misses names that fell
  and never came back. So the true numbers are likely lower than these.

### H2. Volatility risk premium with Tony Cooper's timing rules (short volatility = real SVXY)

| Variant | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD | Feb 2018 | Feb–Mar 2020 |
|---|---|---|---|---|---|---|---|---|
| SPY total return | 14.5% | 1.10 | −19% | 15.6% | 0.82 | −34% | | |
| V1: buy-and-hold SVXY | 11.8% | 0.62 | −93% | 10.4% | 0.46 | −62% | −93% | −61% |
| V2: roll-yield rule, else VIXY | −5.2% | 0.37 | −96% | −4.9% | 0.13 | −83% | −93% | −28% |
| V3: VRP rule, else VIXY | −12.2% | 0.25 | −94% | 22.6% | 0.65 | −50% | −93% | −30% |
| V4: roll-yield rule, else T-bills | 5.0% | 0.52 | −93% | 7.6% | 0.39 | −59% | −93% | −25% |
| V5: VRP rule, else T-bills | 1.4% | 0.46 | −93% | 17.9% | 0.67 | −38% | −93% | −31% |
| V6: VRP rule, 50% SVXY | 9.6% | 0.47 | −61% | 11.7% | 0.76 | −20% | −60% | −17% |

- **Crash risk is the whole story.** Every full-size variant lost 93% in February 2018,
  because both signals were still "short volatility" on Friday 2 February and the trade only
  switches a day later. The real fund fell 32% on 5 February and another 83% on 6 February.
  At a 93% loss, you need a 14x gain to get back to even.
- The rules are short volatility 87% (VRP) and 96% (roll yield) of days. So they mostly
  re-label buy-and-hold rather than time it.
- The hold-out looks better (V3 22.6%, V5 17.9%), but that comes mostly from one year: V3 made
  +130% in 2024, when it happened to be long VIXY into the August spike, and it lost 36% in
  2022 and 21% in 2025. Since February 2018 SVXY has been −0.5×, which halves both the premium
  and the crash.
- Long VIXY as the "off" position (V2, V3) loses money steadily, because contango works
  against the long side in calm periods.

### H3. Ed Thorp style weekly-reversal stat-arb, S&P 500 members

| Variant | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD |
|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | −55% | 15.2% | 0.81 | −34% |
| S1: decile long/short | −4.2% | −0.07 | −76% | −1.4% | 0.08 | −52% |
| S2: quintile long/short | −5.0% | −0.21 | −77% | −0.7% | 0.08 | −48% |
| S3: decile losers, long only | 9.1% | 0.44 | −69% | 15.2% | 0.62 | −60% |
| *Diagnostic: S1 at 0 bps (not tradable)* | *14.6%* | *0.71* | *−29%* | *18.0%* | *0.75* | *−48%* |

- The reversal effect is still there before costs. A market-neutral book long last week's
  losers and short its winners makes 15–18% a year at zero cost, in both periods.
- Costs kill it. Each week the deciles turn over almost completely on both sides, which costs
  about 20% a year at 10 bps per side. Thorp ran this with his own execution and an
  institutional cost base, well below 10 bps. A retail account can't do that.
- The long-only losers book (S3) matches SPY in the hold-out (15.2%) and beats it in-sample
  (9.1% vs 6.6%). But it trails the equal-weight panel (10.6%) and has deeper drawdowns
  (−69% and −60%). It is a high-beta equal-weight portfolio, not an edge.

### H4. Slower reversal: overlapping 2–4 week cohorts and monthly reversal

| Variant | IS CAGR | IS Sharpe | IS MaxDD | HO CAGR | HO Sharpe | HO MaxDD |
|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | −55% | 15.2% | 0.81 | −34% |
| Equal-weight members | 10.6% | 0.60 | −58% | 11.9% | 0.64 | −40% |
| R1: weekly, 2-week cohorts, long/short | 0.9% | 0.14 | −47% | −3.9% | −0.10 | −42% |
| R2: weekly, 4-week cohorts, long/short | 0.8% | 0.13 | −29% | 1.0% | 0.14 | −31% |
| R3: monthly 21-day reversal, long/short | −1.2% | 0.06 | −71% | −9.3% | −0.27 | −64% |
| R4: as R2, long-only losers | 10.6% | 0.50 | −71% | 17.5% | 0.75 | −50% |
| R5: as R3, long-only losers | 8.5% | 0.42 | −83% | 9.1% | 0.44 | −52% |
| *Diagnostic: R2 at 0 bps* | *5.6%* | *0.50* | *−24%* | *5.9%* | *0.47* | *−31%* |
| *Diagnostic: R3 at 0 bps* | *3.0%* | *0.24* | *−58%* | *−5.4%* | *−0.10* | *−54%* |

- **The reversal fades within days.** Four overlapping cohorts cut the cost drag from about
  19 to about 5 points a year, as intended. But the zero-cost return falls from 15–18% (one
  week) to under 6% (four weeks), so most of the edge sits in the first week, and the slower
  book nets about 1%.
- **Monthly reversal is gone in large caps.** R3 loses money even before costs from 2020 on.
  The Fama-French short-term reversal factor has weakened among large caps since the 2000s,
  and this matches that.
- **R4, the long-only 4-week losers book, comes closest so far.** It beats SPY in both
  periods: by 4.0 points in-sample and 2.3 points in the hold-out. It also beats the
  equal-weight basket by 5.6 points in the hold-out. But it fails the bar. Its hold-out
  margin is under 3 points, its hold-out Sharpe (0.75) is below SPY's (0.81), and it lost 71%
  in 2008–09. In-sample it only matches the equal-weight basket (10.6%), so before 2020 the
  reversal added nothing on top of equal-weight market exposure. Calendar years 2020–2026:
  +17%, +35%, −4%, +24%, +15%, +20%, +14%, against SPY's +18%, +29%, −18%, +26%, +25%, +18%,
  +13%.

## What this means for the search

- Short-term reversal has a real edge before costs in both periods (H1 and H3 at 0 bps). But
  it lives in the first few days. Slower versions (H4) keep the costs down and lose most of
  the signal. A retail account can't trade the fast version profitably at 10 bps a side.
- The long-only 4-week losers book (R4) is the strongest result in this project: 17.5% a year
  since 2020, against 15.2% for SPY. It is mostly equal-weight, high-beta market exposure, and
  that comes with a −71% drawdown. It is worth keeping as a candidate. It is not proof of an
  edge.
- Short volatility doesn't add return that survives its crashes. If it is used at all, it
  should be a small sleeve (V6 at 50% still lost 60% in 2018).

## Caveats

- Survivorship: the panel lacks about a third of historical members. The equal-weight
  benchmark carries the same bias, so it is the fairer comparison for H1/H3.
- Costs of 10 bps per side are a flat assumption. Opening-auction fills in large caps can be
  cheaper, while the dips H1 buys often have wider spreads.
- H1 enters at the next open. Parker enters intraday with limit orders and Connors enters at
  the close. Both would capture more of the bounce, but neither can be simulated honestly from
  daily bars.
- H2 has only eight in-sample years, and both periods contain a single dominant event.

Sources: Schwager, *Hedge Fund Market Wizards* (2012, Thorp chapter) and *Unknown Market
Wizards* (2020, Parker chapter; rule summary in
[elearnmarkets](https://www.elearnmarkets.com/school/units/unknown-market-wizards/marsten-parker-don-t-quit-your-day-job));
Connors & Alvarez, *Short Term Trading Strategies That Work* (2008); Cooper,
[*Easy Volatility Investing*](https://www.naaim.org/wp-content/uploads/2013/10/00R_Easy-Volatility-Investing-+-Abstract-Tony-Cooper.pdf)
(2013); Lehmann, "Fads, Martingales and Market Efficiency", QJE (1990).
