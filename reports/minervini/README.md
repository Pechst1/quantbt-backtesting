# Minervini Trend Template on point-in-time S&P 500 members

Run date: 2026-10-02. Period: 1999-01-05 to 2026-09-30, with 1998 used as warm-up. Start capital: $100k, no leverage.
Costs are the engine defaults: 5 bps spread, 2 bps slippage and 5 bps commission per side.
The engine fixes being made in parallel do not touch this strategy's order types (MOO entries and exits, no protective orders).

## Rules (published, fixed before the first full run)

- **Trend Template criteria 1–7** come from Minervini, *Trade Like a Stock Market Wizard*, ch. 5:
  - Price is above the 150-day and 200-day SMAs.
  - The 150-day SMA is above the 200-day SMA.
  - The 200-day SMA is above its value 21 bars ago.
  - The 50-day SMA is above the 150-day and 200-day SMAs.
  - Price is above the 50-day SMA.
  - Price is at least 30% above its 52-week low and within 25% of its 52-week high.
- **Criterion 8:** the RS rank is at least 70.
  - RS is the IBD-style 40/20/20/20 weighting of 3, 6, 9 and 12-month returns, ranked against the S&P 500 members on that day.
  - IBD's own formula is not public, so this is the usual approximation.
- **Universe:** stocks that were S&P 500 members on the signal date.
- **Portfolio:** 10 equal-weight slots, filled with the highest-RS qualifiers and bought at the next open.
- **Exits** happen at the next open when one of these is true:
  - The close is 8% or more below the entry (Minervini caps losses at 10% and averages 6–7%; O'Neil uses 7–8%).
  - The close is below the 50-day SMA.
  - The stock stops trading (delisted or acquired).
- **Variant B** adds a market filter: new buys only while SPY itself passes criteria 1–5. Both variants were declared before running, and they are the only two runs.

The Volatility Contraction Pattern (VCP) pivot entry, fundamentals and position pyramiding are left out because they are discretionary or need data this repo doesn't have.

## Results

| | CAGR | Vol | Sharpe (rf=0) | Max DD | Avg exposure | Trades | Win rate |
|---|---|---|---|---|---|---|---|
| A: Trend Template | 7.4% | 24.9% | 0.41 | −63.5% | 97% | 2,735 | 35% |
| B: Trend Template + SPY filter | 9.8% | 20.8% | 0.55 | −34.3% | 60% | 1,401 | 37% |
| SPY total return | 8.6% | 19.2% | 0.53 | −55.2% | 100% | | |

Excluding the 2026 year-to-date (A +46%, B +69%, SPY +13%), the CAGRs for 1999–2025 are A 6.2%, B 7.9% and SPY 8.4%.

| Sub-period CAGR | A | B | SPY |
|---|---|---|---|
| 1999–2009 | 5.2% | 11.1% | 0.7% |
| 2010–2019 | 5.9% | 3.9% | 13.3% |
| 2020–2026 | 12.9% | 16.0% | 15.1% |

## Data and remaining survivorship bias

- **Prices:** [Johnbrick123/sp500-data](https://github.com/Johnbrick123/sp500-data), using Yahoo, Tiingo and Quandl WIKI history, including delisted names.
  - Membership is reconstructed from the Wikipedia change log.
  - `examples/fetch_sp500_panel.py` downloads it.
- **Coverage:** 830 of 1,222 historical members have prices.
  - Daily coverage of the index is about 60% in 2000, 76% on average in 2003–2009, 95% in 2010–2019 and 99% from 2020.
  - The missing names are mostly old acquisitions and bankruptcies.
- **Size of the bias** (`sp500_panel_bias_check.json`): an equal-weight basket of the members that have prices is compared with RSP, the real equal-weight index.
  - The basket beats RSP by about 2.2%/yr in 2003–2009, 0.7%/yr in 2010–2019 and 0.9%/yr in 2020–2026.
  - Part of that gap is RSP's fee and rebalancing.
  - Results before 2010 are therefore flattered by up to about 2%/yr.

## Reading

- The plain template (A) does not beat buy-and-hold SPY.
  - It is fully invested in 10 stocks, so it takes the full bear-market drawdowns (−46% in 2008) plus whipsaw losses.
  - It lags badly in the low-dispersion 2010s.
- The market filter (B) mostly helps by staying out of 2001–2002 and most of 2008.
  - That roughly halves the drawdown.
  - Its overall edge over SPY depends on the 2026 year-to-date. Through 2025 it trails SPY by about 0.5%/yr, and its best period is also the one with the most survivorship bias.
- Conclusion: this mechanical version of the template is a risk-reduction overlay rather than a source of excess return on S&P 500 large caps.
  - Minervini applied it to a much broader universe of smaller growth stocks, together with the discretionary VCP entries left out here.

Files: `*_summary.json` (metrics, rules, the latest screen and open positions), `*_equity.csv`, `*_trades.csv`.
