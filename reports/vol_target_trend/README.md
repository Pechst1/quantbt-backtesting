# Vol-targeted Turtle and TSMOM, 2008-2026

Run: `python examples/run_vol_target_trend.py` (Yahoo data, about 40 seconds).
Settings were fixed in [PREREGISTRATION.md](PREREGISTRATION.md) and committed before the
first run. All 12 declared variants are below; no other variant was run for this table.

**Result: no variant beats SPY, in-sample or in the hold-out.** The vol target and the
3x cap fixed the blow-up (worst gross leverage 3.1x instead of 40x), but the Turtle
breakout rules lose money on this ETF universe once trading costs are paid, and 12-month
TSMOM earns about half of SPY's return with a lower Sharpe. TSMOM's one clear edge
is drawdown: -24% to -27% versus -52% for SPY.

## In-sample, 2008-01-01 to 2019-12-31

| Strategy | Sizing | CAGR | Sharpe | Max DD | Vol | Avg / max gross |
|---|---|---|---|---|---|---|
| Turtle S1 | published | -36.4% | -0.01 | -99.8% | 94% | 13.0x / 40.5x |
| Turtle S2 | published | -44.1% | -0.15 | -100% | 93% | 11.7x / 37.5x |
| Turtle 50/50 | published | -37.0% | -0.09 | -99.9% | 88% | 12.3x / 33.6x |
| TSMOM | published | 5.4% | 0.39 | -31.0% | 17% | 3.4x / 5.8x |
| Turtle S1 | 15% target | -3.9% | -0.22 | -49.6% | 14% | 1.9x / 3.1x |
| Turtle S2 | 15% target | -2.2% | -0.09 | -48.5% | 14% | 1.7x / 3.1x |
| Turtle 50/50 | 15% target | -3.0% | -0.16 | -44.8% | 13% | 1.8x / 3.1x |
| TSMOM | 15% target | 4.5% | 0.41 | -23.7% | 13% | 2.6x / 3.1x |
| Turtle S1 | 20% target | -5.4% | -0.25 | -59.7% | 17% | 2.2x / 3.1x |
| Turtle S2 | 20% target | -3.8% | -0.13 | -59.8% | 17% | 2.0x / 3.1x |
| Turtle 50/50 | 20% target | -4.4% | -0.20 | -55.7% | 16% | 2.1x / 3.1x |
| TSMOM | 20% target | 4.4% | 0.37 | -27.3% | 15% | 2.8x / 3.1x |
| **SPY** | buy and hold | **9.1%** | **0.54** | -51.9% | 20% | 1.0x |

## Hold-out, 2020-01-01 to 2026-09-30

| Strategy | Sizing | CAGR | Sharpe | Max DD | Vol | Avg / max gross |
|---|---|---|---|---|---|---|
| Turtle S1 | published | -30.7% | -0.03 | -98.4% | 83% | 10.9x / 37.5x |
| Turtle S2 | published | -29.4% | -0.08 | -98.6% | 76% | 8.6x / 32.8x |
| Turtle 50/50 | published | -27.3% | -0.06 | -98.1% | 74% | 9.8x / 32.4x |
| TSMOM | published | 8.5% | 0.59 | -25.3% | 16% | 3.3x / 5.0x |
| Turtle S1 | 15% target | -1.5% | -0.03 | -36.1% | 15% | 1.9x / 3.1x |
| Turtle S2 | 15% target | -1.0% | 0.01 | -39.8% | 15% | 1.7x / 3.1x |
| Turtle 50/50 | 15% target | -1.1% | -0.01 | -36.0% | 14% | 1.8x / 3.1x |
| TSMOM | 15% target | 7.8% | 0.66 | -15.7% | 12% | 2.7x / 3.1x |
| Turtle S1 | 20% target | -4.6% | -0.18 | -48.5% | 17% | 2.2x / 3.1x |
| Turtle S2 | 20% target | -3.5% | -0.10 | -51.3% | 18% | 2.0x / 3.1x |
| Turtle 50/50 | 20% target | -3.9% | -0.15 | -48.8% | 17% | 2.1x / 3.1x |
| TSMOM | 20% target | 7.5% | 0.60 | -21.1% | 14% | 2.9x / 3.1x |
| **SPY** | buy and hold | **15.2%** | **0.80** | -33.7% | 20% | 1.0x |

Full window 2008-2026: SPY 11.3% CAGR, Sharpe 0.64; best variant TSMOM at 15% target,
5.7% CAGR, Sharpe 0.50. All numbers are in `metrics.json`; daily curves in `equity_curves.csv`.

The 20% target realises only 14-18% vol, because the 3x gross cap binds on the
low-volatility bond and currency ETFs.

## Why the Turtle systems lose: trading costs

Diagnostic run after the table above, not part of the pre-registered set: the same
20%-target runs over 2008-2026 at other per-side costs (borrow spread 50 bps throughout).
Cells are CAGR / Sharpe.

| Cost per side | Turtle S1 | Turtle S2 | TSMOM |
|---|---|---|---|
| 0 bps | 5.2% / 0.39 | 3.4% / 0.28 | 7.0% / 0.55 |
| 2 bps | 2.9% / 0.25 | 1.9% / 0.19 | 6.7% / 0.53 |
| 5 bps | -0.4% / 0.06 | -0.4% / 0.07 | 6.3% / 0.50 |
| 9.5 bps (declared) | -5.2% / -0.23 | -3.7% / -0.12 | 5.6% / 0.45 |

Turtle S1 trades about 100 round trips a year across 14 ETFs, each pyramided up to 4
units, at around 2x gross, so every basis point of cost costs it about 1% a year. Even
free of costs neither Turtle system nor TSMOM reaches SPY's Sharpe of 0.64 here, so a
higher vol target or cap would add risk faster than return. The 50 bps borrow spread costs
about 0.2% a year.

## Method notes and limits

- The overlay holds `k` times the published book; `k` = target / EWMA vol of the
  published book's daily return (60-day center of mass), set at the close, applied at the
  next open, and capped so gross stays at or under 3x (entries and adds are trimmed
  intraday). Code: `src/quantbt/systems/overlay.py`; the Turtle and TSMOM simulators take
  `vol_target=`.
- The book is rescaled every day, and those trades pay costs too.
- ETFs stand in for the futures the Turtles traded. Currency and commodity ETFs have
  wider spreads and carry costs (contango in USO/UNG/UGA) that futures traders avoid.
  Short positions earn the bill rate and pay no borrow fee, which flatters the shorts.
- Daily bars: a bar's entry fills before its stop is checked against the same bar, so
  same-day stop-outs are assumed whenever the range allows (3-5% of trades).
