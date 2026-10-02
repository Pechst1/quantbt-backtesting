# Market Wizards (1989): Schwartz, Seykota/Hite and Rogers, tested as written

**Result: none of the 9 pre-registered strategy variants (plus one control) beats SPY in the 2020-2026 hold-out, and none
beats it by a wide margin in-sample either.** Two have a better risk-adjusted profile than SPY
before 2020 (Seykota's crossover on SPY, Rogers' "depressed and turning up" on stocks), but both
trail SPY by 7-8 points a year since 2020.

Rules and variants were committed in [PREREGISTRATION.md](PREREGISTRATION.md) before the first
run. Every variant is reported. Parameters come from the traders' own statements; nothing was
changed after seeing results. Two code fixes were made after the first run, neither touching a
rule: the R3 control now starts on the same date as R1/R2 (it had started in 1998 holding SPY
alone), and K3's reporting window now starts when membership data exists (1996) instead of 1977.

Reproduce: `python examples/fetch_sp500_panel.py && python examples/run_market_wizards_1989.py`.

## Results

Signals at the close, fills at the next open, 10 bps per side (K1/K2: Seykota's 50% skid),
idle cash earns 3-month T-bills, borrowing pays T-bill + 1%. Sharpe uses rf = 0 (excess-return
Sharpe is in each `*_summary.json`). SPY is measured over the same dates as each variant.

| Variant | In-sample | CAGR | Sharpe | Max DD | SPY CAGR / Sharpe / DD | Hold-out 2020-26 CAGR | Sharpe | Max DD | SPY CAGR / Sharpe / DD | Avg exposure | Turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 Schwartz 10d EMA, SPY long/cash | 1994-2019 | -0.9% | -0.02 | -59.6% | 9.7% / 0.60 / -55.2% | -0.7% | 0.01 | -37.2% | 15.1% / 0.80 / -33.7% | 0.63 | 46.4x |
| S2 Schwartz 10d EMA, SPY long/short | 1994-2019 | -11.6% | -0.61 | -96.6% | 9.7% / 0.60 / -55.2% | -15.4% | -0.78 | -74.9% | 15.1% / 0.80 / -33.7% | 0.99 | 93.1x |
| S3 Schwartz 10d EMA, QQQ long/cash | 2000-2019 | -5.0% | -0.22 | -83.1% | 6.0% / 0.40 / -55.2% | 1.2% | 0.16 | -42.1% | 15.1% / 0.80 / -33.7% | 0.61 | 47.4x |
| K1 Seykota 15/150 EMA on SPY, published sizing | 1995-2019 | 11.5% | 0.73 | -33.5% | 10.1% / 0.61 / -55.2% | 6.7% | 0.47 | -35.1% | 15.1% / 0.80 / -33.7% | 1.08 | 3.3x |
| K2 K1 capped at 1x | 1995-2019 | 8.4% | 0.71 | -25.6% | 10.1% / 0.61 / -55.2% | 7.2% | 0.59 | -30.0% | 15.1% / 0.80 / -33.7% | 0.77 | 2.3x |
| K3 Seykota 15/150 on S&P stocks, Hite 1% risk | 1997-2019 | 8.2% | 0.60 | -49.5% | 8.5% / 0.52 / -55.2% | 8.1% | 0.55 | -32.9% | 15.1% / 0.80 / -33.7% | 0.99 | 9.8x |
| R1 Rogers: 4 most depressed countries, turned up | 2002-2019 | 5.4% | 0.49 | -24.3% | 7.9% / 0.51 / -55.2% | 8.5% | 0.85 | -18.6% | 15.1% / 0.80 / -33.7% | 0.54 | 3.5x |
| R2 R1 without the turn-up filter | 2002-2019 | 6.3% | 0.38 | -58.6% | 7.9% / 0.51 / -55.2% | 12.1% | 0.65 | -40.9% | 15.1% / 0.80 / -33.7% | 1.00 | 3.1x |
| R3 control: equal-weight country funds | 2002-2019 | 7.8% | 0.46 | -61.6% | 7.9% / 0.51 / -55.2% | 10.9% | 0.62 | -37.5% | 15.1% / 0.80 / -33.7% | 1.00 | 0.4x |
| R4 Rogers on S&P stocks: 20 worst 3y, turned up | 1999-2019 | 7.5% | 0.70 | -20.7% | 6.6% / 0.43 / -55.2% | 7.9% | 0.65 | -19.5% | 15.1% / 0.80 / -33.7% | 0.30 | 2.7x |

## What each hypothesis showed

**Schwartz (10-day EMA): rejected.** The rule switches about 46 times a year. Before costs and
cash interest it made about 2.9% a year on SPY since 1994 (checked with an independent
vectorised calculation), against SPY's 9.7%: almost all of SPY's return since 1993 came
overnight (9.9%/yr close-to-open vs 0.9%/yr open-to-close), and a rule that acts at the next
open gives up the gap that triggered it. Costs then take another ~4.6%/yr. The long/short
version loses heavily. Whatever worked for Schwartz in 1980s S&P futures, at his discretion,
does not survive as a mechanical daily rule on today's index.

**Seykota exponential crossover: the best of the set, still no edge since 2020.** On SPY with
his published sizing (10% heat over 5 x ATR20, about 1.1x average exposure), it beat SPY from
1995 to 2019 (11.5% vs 10.1%, max drawdown -34% vs -55%). In the hold-out it made 6.7% vs 15.1%:
it lost 4.6% in 2020 (out after the crash, back in late) against SPY's +17%, and 32% in 2022
against SPY's 19%, as whipsaws hit a position above 1x. Capped at
1x (K2) it keeps the smaller drawdowns but gives up return in both periods. Spread across
S&P 500 stocks with Hite's 1% risk per position (K3), it matched SPY before 2020 at a slightly
better Sharpe and trailed after; with ~300 names in uptrend it ends up close to an inverse-
volatility index, and weekly rebalancing costs about 1%/yr.

**Rogers (depressed and turning up): lower risk, not higher return.** On country funds the
"turned up" filter (R1) halves the drawdown of buying the losers outright (R2), but neither
beats an equal-weight basket of the same funds (R3) or SPY in-sample. On S&P stocks (R4) it
had the best in-sample risk-adjusted numbers (7.5% vs SPY 6.6%, drawdown -21% vs -55%) while
only 30% invested on average, because few losers are above their 10-month average at once.
That edge is overstated: the panel is missing about a third of past members, mostly the
losers that went bankrupt or were bought cheaply, which are exactly what R4 would have held.
In the hold-out it made 7.9% vs 15.1%.

## Takeaways for the next round

- Across this round and the earlier ones, rules with trend or "turn-up" filters cut drawdowns
  by about half but cost return, and all of them trail SPY since 2020. Nothing here is a
  candidate for "beats SPY by a wide margin".
- The cleanest risk reducers (K2, R4) could be levered to SPY's volatility, but earlier PR #7
  showed leverage adds no return edge on its own, and these hold-out Sharpes (0.59, 0.65) are
  below SPY's 0.80, so leverage would not beat SPY there either.

## Files

- `PREREGISTRATION.md`: rules, written and committed before any run.
- `<variant>_summary.json`: in-sample, hold-out and full-period stats, SPY over the same
  dates, calendar-year returns, exposure and turnover.
- `<variant>_equity.csv`: daily equity and gross exposure.
- Code: `examples/run_market_wizards_1989.py` (signals), `src/quantbt/weights_sim.py`
  (next-open target-weight simulator with costs, T-bill cash, financing, delisting exits;
  tested in `tests/test_weights_sim.py`).
