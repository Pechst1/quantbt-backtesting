# Relative-strength leaders on point-in-time S&P 500 members

Run date: 2026-10-02. One continuous run per variant from 1998-01 (warm-up) to 2026-09-30,
$100k start, no leverage, engine default costs, cash earns nothing. Rules and the variant list
were committed in [PREREGISTRATION.md](PREREGISTRATION.md) before the first run, and every
variant run is reported below. Engine: the conservative-fill fixes from PR #1.

## Rules in one paragraph

On the close of each month's first trading day, rank S&P 500 members (point-in-time) by 12-1
month momentum; if SPY is above its 200-day average, hold the top 10 (new names get equity/10,
holdings are not resized), otherwise go to cash. Any holding that closes 8% below its entry is
sold at the next open (O'Neil's loss cut). All orders fill at the next open.

## Results

| | 1999–2019 CAGR | Sharpe | Max DD | 2020–2026 CAGR | Sharpe | Max DD | Full CAGR | Sharpe | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| SPY total return | 6.6% | 0.43 | -55% | 15.1% | 0.80 | -34% | 8.7% | 0.53 | -55% |
| Equal-weight panel members | 10.8% | 0.61 | -57% | 11.9% | 0.64 | -40% | 11.0% | 0.62 | -57% |
| A: top 10, no filter, no stop | 8.1% | 0.41 | -71% | 22.5% | 0.82 | -36% | 11.5% | 0.51 | -71% |
| B: top 10 + SPY filter | 15.5% | 0.73 | -35% | 5.1% | 0.32 | -32% | 12.9% | 0.61 | -52% |
| **C: top 10 + filter + 8% stop (headline)** | 13.2% | 0.70 | -31% | 4.4% | 0.30 | -29% | 11.1% | 0.59 | -40% |
| D: top 5 + filter + stop | 11.6% | 0.57 | -44% | 5.3% | 0.32 | -34% | 10.1% | 0.50 | -49% |
| E: top 20 + filter + stop | 10.4% | 0.62 | -29% | 2.5% | 0.22 | -32% | 8.4% | 0.52 | -40% |
| F: IBD RS top 10 + filter + stop | 14.6% | 0.71 | -35% | 2.9% | 0.24 | -41% | 11.7% | 0.59 | -55% |

Sharpe uses rf = 0. 2020–2026 runs to 2026-09-30. "Equal-weight panel members" is every
covered member, rebalanced monthly; it carries the same survivorship bias as the strategies,
so the gap to it is the cleaner measure of what the ranking adds.

Independent check (`examples/rs_momentum_vector_check.py`, no engine, no costs, full monthly
rebalance, no stop): A-like 9.0% / 26.9% and B-like 13.5% / 9.8% for 1999–2019 / 2020–2026.
Same picture as the engine runs.

## Reading

- **In-sample (1999–2019) the filtered versions look excellent**: B makes 15.5% a year against
  6.6% for SPY with a −35% worst drawdown, and still beats the equal-weight panel by about
  4.7%/yr. The filter does all the risk work: it sat out most of 2001–02 and 2008 (B +0.8% in
  2008 vs SPY −36%, A −66%).
- **The hold-out breaks it.** From 2020 every filtered variant makes 2.5–5.3% a year against
  15.1% for SPY. The filter sold near the March 2020 and 2022 lows and re-entered late, and
  the 8% stop repeatedly cut leaders in volatile but rising markets (C: 540 of 1,191 trades
  ended on the stop). Calendar years: C −2% in 2020, −9% in 2021, +4% in 2023, −3% in 2025,
  while SPY made +17%, +31%, +27%, +18%.
- **Unfiltered momentum (A) shows the reverse**: it beats SPY in the hold-out (22.5%), but most
  of that is 2026 (+96% year to date, a memory/semiconductor run: SNDK, MU, WDC, STX); through
  2025 it roughly tracks SPY. In-sample it lost 66% in 2008 and trailed SPY's Sharpe.
- **Concentration and ranking choice don't rescue it.** Top 5, top 20 and the IBD-style RS
  score all land in the same place: strong 1999–2019, weak 2020–2026.
- **Bottom line:** no pre-registered variant beats SPY in both periods. Mechanical O'Neil-style
  leadership with a market filter and an 8% stop worked brilliantly in 1999–2019 and failed out
  of sample; the in-sample edge is real relative to the equal-weight panel but is also the period
  with the most survivorship bias.

## Caveats

- The panel lacks about a third of historical members (mostly pre-2010 acquisitions and
  bankruptcies). An equal-weight basket of covered names beats RSP by ~2.2%/yr in 2003–09 and
  ~0.7–0.9%/yr after 2010 (`reports/minervini/sp500_panel_bias_check.json`). A momentum book
  is less exposed than an equal-weight one (losers rarely rank top 10), but 1999–2009 numbers
  are still flattered.
- Held names that stop trading keep their last price (7 such positions in A, 2–7 elsewhere).
- One data-hygiene rule was added after the smoke test and before the full runs: the membership
  history lists a few companies twice at once (KORS/CPRI, PX/LIN, CCE/CCEP), so a candidate
  with the same price and score as one already chosen is skipped. It changes no parameter.
- Cash earns nothing; at T-bill rates the filtered variants would gain roughly 0.5–1%/yr.

## Files

`rs_leaders_{A..F}_summary.json` (metrics, calendar years, exits, current leaders),
`*_equity.csv`, `*_trades.csv`, `benchmarks_*`, `vector_check.json`.
Reproduce: `python examples/fetch_sp500_panel.py`, then
`python examples/run_rs_momentum.py --benchmarks` and `--variant A` … `--variant F`.
