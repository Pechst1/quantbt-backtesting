# New Market Wizards: pre-registered rules (written before any run)

Date: 2026-10-02. Committed before the first backtest. Every variant listed here is run and reported, whatever the result. No parameter below is chosen from the data; where the published rule is vague, the choice is fixed here and not revisited.

## Source and choice of traders

Schwager, *The New Market Wizards* (1992). Already tested in this repo and skipped here: Turtle/Dennis breakouts, 12-month trend following, the Tudor Jones 200-day rule, O'Neil/Ryan momentum, Minervini's trend template, vol-targeted trend (Basso/Eckhardt style sizing, PR #8).

Of the remaining traders, only **Linda Bradford Raschke** published exact, mechanical entry rules: Connors & Raschke, *Street Smarts* (1995). Two of its setups are tested:

1. **Turtle Soup Plus One** (fade a failed 20-day breakdown). Raschke credits the idea to Victor Sperandeo's "2B" rule (*Trader Vic*, 1991), another New Market Wizard, so this test also covers Sperandeo's 2B.
2. **Holy Grail** (buy the first pullback to the 20-period EMA in a strong ADX trend).

Not tested, and why: Sperandeo's 1-2-3 rule needs a hand-drawn trendline; Trout, Lescarbeau and Hull never published rules; Druckenmiller, Lipschutz, Weiss and Yass are discretionary; Gil Blake's fund-timing edge came from stale mutual-fund NAVs that no longer exist.

## Hypotheses

- **H1 (Turtle Soup +1):** in S&P 500 stocks, a close below a 20-day low that was set at least three sessions earlier is a trap for breakout sellers, and a buy stop back at that low catches a short-term reversal. If true, the long-only portfolio beats SPY on CAGR and Sharpe in both 1998-2019 and 2020-2026.
- **H2 (Holy Grail):** when ADX(14) is above 30 with +DI above -DI, the first pullback to the 20 EMA is bought, and price returns to the recent swing high more often than it breaks the pullback low by enough to beat SPY in both periods.

## Exact rules (long only)

Daily bars, split- and dividend-adjusted (the engine's default). "Tick" offsets are dropped (daily stock prices have no tick).

### Turtle Soup Plus One

- Day 1 (signal bar t): let L = lowest low of bars t-20 … t-1 (the prior 20-day low) and k = the bar where it was set. Setup when low_t < L, close_t ≤ L and k ≤ t-3 (set at least three sessions earlier).
- Day 2: buy stop at L, good for that day only. A gap open above L fills at the open.
- Initial stop: the lower of the day-1 and day-2 lows, placed at the day-2 close, as a sell stop for the next bar.
- Trailing stop (Raschke: "trail a stop"): at each later close, the stop moves up to the prior bar's low if that is higher. Never lowered.
- Time exit: Street Smarts calls these 2-6 day trades, so a position still open after 6 bars is sold at the next open.
- Ranking when setups exceed free slots: deepest close below L in percent first ("the lower the better").

### Holy Grail

- Setup bar t: ADX(14) > 30 (Wilder), +DI(14) > -DI(14), and low_t ≤ EMA20_t (touch of the 20 EMA).
- Next bar: buy stop at high_t, good for one day. If that bar is again a setup bar and the order did not fill, the stop is re-armed at its high.
- Initial stop: lowest low from the setup bar through the entry bar ("the newly formed swing low"), placed at the entry close. Fixed after that.
- Target: sell limit at the highest high of the 20 bars before the setup bar ("the recent swing high"). A setup whose target is not above its entry stop is skipped. No time exit. On a bar that touches both, the stop is assumed first.
- Ranking: highest ADX first.

### Common to both

- Stop and target orders are only live from the bar after they are set (the engine cannot know intraday order on the entry bar). Exits that can't fill wait for the next real bar. Delisted names are sold at the next open after their last bar.
- Engine costs from PR #1 (5 bps spread, 2 bps slippage, 5 bps commission plus fees: about 9.5 bps per side). Idle cash earns the 3-month T-bill rate (FRED DTB3). No leverage, no shorting.

## Variants (all six are run and reported)

| ID | Setup | Universe | Slots |
|---|---|---|---|
| TS-SPX | Turtle Soup +1 | point-in-time S&P 500 members (panel from PR #4) | 10 × 10% |
| TS-SPX-200 | Turtle Soup +1, only if close_t > its 200-day SMA (trade with the larger trend) | same | 10 × 10% |
| HG-SPX | Holy Grail | same | 10 × 10% |
| TS-ETF | Turtle Soup +1 | 18 ETFs below | 5 × 20% |
| TS-ETF-200 | Turtle Soup +1 with the 200-day filter | 18 ETFs | 5 × 20% |
| HG-ETF | Holy Grail | 18 ETFs | 5 × 20% |

ETF universe (a stand-in for Raschke's futures portfolio): SPY, QQQ, IWM, MDY, DIA, EFA, EEM, TLT, GLD, XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY. Each trades once it has 200 days of history.

## Periods and reporting

- Backtest 1998-01-01 to 2026-09-30.
- In-sample: 1998-2019. Hold-out: 2020-01-01 to 2026-09-30. The hold-out is shown separately next to SPY total return.
- Reported for each period: CAGR, Sharpe (excess over T-bills), max drawdown, average exposure, trade count, win rate, average trade.
- Pass criterion for "beats SPY": higher CAGR **and** higher Sharpe than SPY in both periods.
