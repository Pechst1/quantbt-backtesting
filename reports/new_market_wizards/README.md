# New Market Wizards: Raschke's Turtle Soup +1 and Holy Grail

**Result: rejected.** None of the six pre-registered variants beats SPY in either period. Every variant made between -2.2% and +1.8% a year from 1998 to 2026, while SPY made 9.3%. The rules and variants were committed before the first run (see [PREREGISTRATION.md](PREREGISTRATION.md)). Nothing was changed after seeing results.

## What was tested

Linda Bradford Raschke is the only trader in *The New Market Wizards* who published exact mechanical setups (Connors & Raschke, *Street Smarts*, 1995). Two of them were tested:

- **Turtle Soup Plus One:** buy back above a failed 20-day breakdown. This is Raschke's version of Victor Sperandeo's "2B" rule, so it also tests Sperandeo.
- **Holy Grail:** buy the first pullback to the 20 EMA when ADX(14) is above 30.

Both were run long only. Each was tested on point-in-time S&P 500 members (10 slots of 10% each) and on 18 index, sector, bond and gold ETFs (5 slots of 20% each). Turtle Soup also has a variant that only buys above the 200-day average.

Setup:
- Engine: the PR #1 fill rules, about 9.5 bps per side in costs.
- Orders: one-day buy and sell stops.
- Cash: earns T-bill interest (FRED DTB3).
- Sharpe: measured as excess return over T-bills.

## Results

The hold-out (2020-01-01 to 2026-09-30) was never used for any choice.

| Variant | 1998-2019 CAGR | Sharpe | Max DD | 2020-26 CAGR | Sharpe | Max DD | Avg exposure | Trades | Win rate |
|---|---|---|---|---|---|---|---|---|---|
| **SPY (total return)** | **7.5%** | **0.38** | **-55%** | **15.1%** | **0.66** | **-34%** | 100% | — | — |
| TS-SPX | -2.2% | -0.24 | -60% | 2.5% | 0.02 | -16% | 43% | 11,019 | 40% |
| TS-SPX-200 | -1.7% | -0.42 | -52% | -3.9% | -0.67 | -26% | 28% | 8,080 | 40% |
| HG-SPX | -0.4% | -0.13 | -47% | -2.0% | -0.62 | -20% | 64% | 6,298 | 43% |
| TS-ETF | -0.3% | -0.47 | -33% | 2.1% | -0.09 | -14% | 8% | 1,179 | 41% |
| TS-ETF-200 | 1.4% | -0.26 | -8% | 2.8% | -0.01 | -4% | 4% | 597 | 41% |
| HG-ETF | 2.1% | 0.07 | -5% | 1.1% | -0.70 | -7% | 5% | 380 | 44% |

Full-period numbers, trade lists and equity curves are in `<variant>_summary.json`, `_trades.csv` and `_equity.csv`. Rerun with `python examples/fetch_sp500_panel.py && python examples/run_raschke_setups.py`.

## Why the setups fail

- **The edge is smaller than trading costs.** Before costs the average trade makes +0.05% to +0.22%. Round-trip costs are about 0.25%, so every variant loses about 0.03% to 0.20% per trade after costs. With 400 trades a year on stocks, that alone costs several percent a year.
- **The ETF versions are mostly in cash** (4-8% exposure). The setups rarely trigger on 18 ETFs. Even a real edge could not lift returns near SPY's without heavy leverage, and the per-trade numbers show no edge to lever.
- **The trend filter does not help.** Trading Turtle Soup only above the 200-day average made results worse on stocks. That matches the earlier momentum tests in this repo.

## Caveats

- Daily bars only. Raschke traded these setups intraday on futures with tick-level stops, where costs are lower and the entry-day stop can work. Here a stop only becomes active the bar after it is set, and when a bar touches both the stop and the target, the stop counts first. Both choices are conservative.
- The exits for Turtle Soup (trail to the prior low, sell after 6 bars) were fixed before the run, because the book's "trailing stop" is not exact. The edge before costs is small enough that a different trail is unlikely to cover about 25 bps a trade.
- The S&P 500 panel still has some survivorship bias, about +1% a year (see PR #4). It would flatter these results, not hurt them.
- Names that are delisted while held are marked at their last price.

## Not tested, and why

- Sperandeo's 1-2-3 rule needs a hand-drawn trendline.
- Trout, Lescarbeau and Hull never published their rules.
- Druckenmiller, Lipschutz, Weiss and Yass trade discretionarily.
- Gil Blake's fund-timing edge came from stale mutual-fund prices that no longer exist.
- Basso-style volatility sizing was already tested in PR #8.
