# Relative-strength leaders: rules fixed before the first run

Written 2026-10-02, before any backtest of this strategy was run. Every variant listed here
is run once and reported, whatever it shows. No parameter is changed after seeing results.

## Idea

William O'Neil and David Ryan (*Market Wizards*, *Stock Market Wizards*) buy the market's
strongest stocks, only in a confirmed uptrend, and cut every loss at 7-8%. The academic
version of the same effect is 12-1 month momentum (Jegadeesh & Titman 1993). This test
combines them in a fully mechanical form on S&P 500 large caps.

## Rules (headline variant C)

- **Universe:** stocks that are S&P 500 members on the signal date (point-in-time membership,
  `data/index_membership/sp500_membership_pit_panel.csv`) and have prices in the panel.
- **Signal date:** the close of the first trading day of each month. Orders fill at the next open.
- **Ranking:** 12-1 month momentum, the total return from 252 to 21 trading days before the
  signal date. Needs 253 bars of history.
- **Market filter:** SPY close above its 200-day simple moving average on the signal date.
  If it is not, every position is sold and the portfolio holds cash until a later signal date
  passes the filter.
- **Holdings:** the top 10 by momentum. Names that fall out of the top 10 (or out of the index)
  are sold. New names get an equal slot of current equity / 10, funded from cash. Positions
  still in the top 10 are not resized, so winners can grow.
- **Loss cut:** a position whose daily close is 8% or more below its entry price is sold at the
  next open (O'Neil's 7-8% rule). The slot stays in cash until the next signal date. A stopped
  name can be bought again at a later signal if it is still a leader.
- **Delisting:** a held name with 5 consecutive missing bars is marked dead; its last price
  stays in equity and one sell order is left pending.
- **Costs:** engine defaults (5 bps spread, 2 bps slippage, 5 bps commission, fees), no leverage,
  cash earns nothing.

## Variants (all run, all reported)

| Variant | Ranking | Top N | SPY filter | 8% stop |
|---|---|---|---|---|
| A | 12-1 momentum | 10 | no | no |
| B | 12-1 momentum | 10 | yes | no |
| C (headline) | 12-1 momentum | 10 | yes | yes |
| D | 12-1 momentum | 5 | yes | yes |
| E | 12-1 momentum | 20 | yes | yes |
| F | IBD-style RS (40/20/20/20 of 3/6/9/12-month returns) | 10 | yes | yes |

Benchmarks: SPY total return, and an equal-weight basket of all panel members (monthly
rebalance), which shares the panel's survivorship bias and so isolates the selection effect.

## Periods

One continuous run from 1998-01 (1998 is warm-up) to 2026-09. Reported separately:
in-sample 1999-2019 and hold-out 2020-01 to 2026-09, plus the full period. Metrics: CAGR,
Sharpe (rf = 0), max drawdown.

## Known caveat

The panel misses about a third of historical members, mostly pre-2010 acquisitions and
bankruptcies. An equal-weight basket of the covered names beats RSP by about 2.2%/yr in
2003-2009 and about 0.7-0.9%/yr after 2010 (`reports/minervini/sp500_panel_bias_check.json`).
