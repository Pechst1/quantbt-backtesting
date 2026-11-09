# Best-strategy search: rules, variants and the selection rule, fixed before any run

Written and committed on 2026-11-09, before any backtest in this report was run. Nothing
below changes after results are seen. Every variant is reported. The reasoning behind each
change is in the project's "Strategy review and improvement hypotheses" document; the changes
come from the traders' own rules and from published research, not from earlier backtest numbers.

## Data

- S&P 500 point-in-time members and prices from `examples/fetch_sp500_panel.py` (upstream
  refreshed on 2026-11-09; the membership CSV is regenerated to match). SPY is traded from the
  same panel. ^IRX (T-bills) and ^VIX from Yahoo.
- Known flaw, unchanged: the panel lacks about 40% of members in 1999 and about 10% in 2008,
  mostly names that collapsed. 1999-2009 is therefore reported but **not used to pick the
  winner**, except as a drawdown check.
- Trend sleeve: the 25-ETF universe of `quantbt.systems.tsmom`, Yahoo, usable from 2008.

## Timing, costs and financing (all variants)

- Decisions use the close; trades fill at the next open. A name that leaves the data is sold
  at its last close.
- One continuous run per variant from 1998-01-02 (1998 is warm-up); scored from 1999-01-04.
- Headline cost, "Scalable": EUR 1.99 per order on a EUR 25,000 account (charged as that
  fraction of equity, 0.8 bps per order, so the fee drag never shrinks as the account grows),
  plus a half-spread of 25 bps for ordinary members and 6 bps for SPY and the 50 largest
  members by 63-day dollar volume (the spreads measured at Scalable on 2026-10-02).
- Secondary cost, "US broker": 0.4 bps of equity per order plus 3 bps half-spread.
- Diagnostic: zero cost.
- Idle cash earns T-bills. Borrowed cash pays T-bills + 3% a year.
- A position is only adjusted when it is more than 10% (relative) away from its target.
- Currency effects (EUR account, USD assets) and taxes are ignored.

## Building blocks

**Momentum book.** On the close of each month's first trading day, rank the members with 252
days of history. Keep every held name that still ranks in the top 30; fill the book back up to
10 names with the best-ranked names not held. Equal weight. No stop-loss.
Ranking A: 12-1 month return (close 21 days ago / close 252 days ago).
Ranking B: closeness to the 52-week high (close / highest close of the past 252 days).

**Volatility scaling** (Barroso and Santa-Clara; Moreira and Muir). Exposure = target volatility /
realised volatility of the unscaled momentum book's own daily returns over the past 63 trading
days, capped. Checked on each week's last close and at each monthly rebalance; traded only when
the wanted exposure differs from the current one by more than 0.10.

**Buffered 200-day rule.** On each week's last close: go to cash when SPY closes more than 3%
below its 200-day average; go back in when it closes more than 3% above it.

**Panic state.** Mark Cook's breadth buy state, exactly as coded in
`quantbt.research.megacap_reversal.cook_breadth_state` (unchanged from PR #11/#14), checked daily.
The wider trigger adds: ^VIX closes above the 80th percentile of its own previous 252 closes.

**Panic reversal book** (Lehmann; Nagel, "Evaporating Liquidity"). Among the 50 largest members
by 63-day dollar volume, hold the 5 with the lowest 5-day return relative to SPY, equal weight.
Formed on the first close of a panic and re-formed on each week's last close while it lasts.

**Trend sleeve.** `quantbt.systems.tsmom` sizing (40% / volatility per instrument, 9.5 bps costs,
bill return on cash). Signal: the average of the signs of the 1-, 3- and 12-month excess
returns, with negative values set to zero (long or flat, since Scalable cannot short). Each of
the four asset classes gets a quarter of the book. Monthly.

## Variants (all reported)

| | Rule |
|---|---|
| P1 | Momentum book A, always fully invested |
| P2 | P1 with volatility scaling: target 20%, cap 1.0x |
| P3 | P1 with volatility scaling: target 30%, cap 2.0x |
| P4 | P2 with ranking B instead of A |
| P5 | P1 with the buffered 200-day rule instead of volatility scaling |
| R1 | SPY; 1.5x SPY while Cook's state is on |
| R2 | SPY; the panic reversal book instead while Cook's state is on |
| R3 | R2 with the wider trigger (Cook or VIX) |
| C1 | P2; while Cook's state is on, the capital that volatility scaling left idle goes into the panic reversal book |
| T1 | Trend sleeve alone (2008+) |
| C2 | 70% C1 + 30% T1, rebalanced monthly, the mix scaled to a 20% volatility target (63-day), cap 2.0x, 10 bps on changes in scale, financing as above (2008+; combined from the daily return series, so an approximation) |

Benchmarks: SPY total return, and the equal-weight basket of all covered members.

## Selection rule

Scored at "Scalable" costs on three windows: 1999-2009, 2010-2019, 2020-01-01 to 2026-09-30.
2020-2026 has been looked at by earlier threads, so it is a check, not a clean hold-out.

A variant **qualifies** if all of these hold:
1. CAGR above SPY's in 2010-2019 **and** in 2020-2026.
2. Worst drawdown over the whole scored run no deeper than -50% (SPY's own was -55%).
3. Not ruined, and at least 2010-2026 scored (C2 and T1 are judged from 2008).

**The winner is the qualifying variant with the highest CAGR over 2010-01-01 to 2026-09-30.**
If nothing qualifies, the report says so and names the closest variant, without relaxing the rule.

After the winner is fixed, and only for it, these are reported as robustness checks. They cannot
change the winner: volatility windows of 21 and 126 days; books of 5 and 20 names; rebalancing on
the 11th trading day of the month; the two other cost settings; and the days after 2026-09-30
that no thread has seen.
