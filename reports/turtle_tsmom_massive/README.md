# Turtle S1/S2 and 12-month TSMOM on Massive data

Run: `python examples/run_turtle_tsmom.py --source massive --start 2021-10-04 --eval-start 2022-11-01 --end 2026-10-01 --out reports/turtle_tsmom_massive`

The Massive plan only serves daily bars from 2021-10-04 onward (a rolling ~5-year window; earlier requests return NOT_AUTHORIZED), so the declared 2004 start and 2008-2019 in-sample window are not reachable. Scoring starts 2022-11-01, the first trade after TSMOM's 12-month warm-up; the whole window lies in the 2020+ hold-out. Published parameters, nothing tuned.

| Strategy | CAGR | Sharpe | Max DD | Vol | Avg gross lev |
|---|---|---|---|---|---|
| Turtle System 1 | -48.4% | -0.17 | -95.0% | 98% | 14.3x |
| Turtle System 2 | -69.7% | -0.35 | -99.7% | 122% | 14.0x |
| Turtle 50/50 | -56.6% | -0.29 | -98.4% | 102% | 14.1x |
| TSMOM 12m, 25 ETFs | 4.5% | 0.37 | -23.1% | 15% | 3.5x |
| SPY buy and hold | 20.9% | 1.31 | -18.8% | 15% | 1.0x |

Turtle sizing (1% of equity per N) on low-volatility ETFs such as the currency trusts gives ~2x notional per unit, so the portfolio runs at ~14x gross. During warm-up (2022) both Turtle systems roughly tripled; from Nov 2022 they gave it all back and more.
