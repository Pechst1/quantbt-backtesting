# Current Market Wizards Macro Research Baseline

Baseline command:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

The `--barbell-research-baseline` preset expands to:

- `--barbell-extreme-concentration-layer`
- `--barbell-phase3-profit-rate-layer`
- `--barbell-overlay-off`

Latest proxy-data result:

| Variant | CAGR | Sharpe | Sortino | Max DD | Calmar | Win Rate | Profit Factor | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Research baseline | 12.2091% | 0.8881 | 1.0521 | -23.9980% | 0.5088 | 61.41% | 4.5340 | 526 |
| Research baseline + liquidation reversal | 12.2922% | 0.9008 | 1.0621 | -23.9965% | 0.5122 | 60.63% | 4.4334 | 574 |
| Baseline + convex proxy | 11.8578% | 0.8444 | 0.9854 | -24.9178% | 0.4759 | 61.35% | 4.1806 | 533 |
| Radical stack | 10.2199% | 0.7258 | 0.8613 | -27.3419% | 0.3738 | 49.36% | 2.7541 | 1,714 |

Current best proxy-data command:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-research-baseline \
  --barbell-liquidation-reversal-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Radical-stack command:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-radical-research-layer \
  --start 2005-01-01 --end 2025-01-01 \
  --interval 1d --no-report
```

Notes:

- The convex dislocation sleeve is implemented as a linear-underlying proxy, not real option pricing. On the current proxy macro data it did not improve the baseline, so it remains experimental.
- The liquidation-reversal sleeve is implemented as a stateful crash-rebound add-on for dislocation instruments other than `UUP`: it requires a 21-day drawdown of at least 12%, a 5-day/high or SMA10 confirmation, then holds the add-on up to 40 bars unless price loses SMA10 or breaches a 7% sleeve stop. This is currently the only accretive radical sleeve on proxy data, but its effect is small: active ratio is 0.44% and mean gross is 0.37%.
- The radical stack combines crisis trend, dollar squeeze, and liquidation reversal. It is rejected as a production baseline on the current data because crisis trend and dollar squeeze increase churn and materially reduce CAGR and profit factor.
- Crisis trend is a price-only stress-following sleeve that allocates to the strongest 1/3/6-month standardized trends only when Engine A/credit stress, EMB stress, HYG drawdown, or extreme macro coherence is active. It remains opt-in because it reduced the 2005-2025 proxy-data CAGR versus the research baseline.
- Dollar squeeze is a USD/EM/commodity stress sleeve that buys `UUP` and shorts EM/commodity proxies when `UUP` and/or credit stress spikes. It remains opt-in because the proxy-data result showed too much false-positive churn and deeper drawdown.
- The futures proxy path is implemented with Yahoo continuous futures tickers (`CL=F`, `GC=F`, `HG=F`) and security-master support. The runner now treats those tradeable futures symbols as required, so the futures run correctly fails if they are unavailable instead of silently running a different universe. In this sandbox the run failed at `CL=F` because Yahoo DNS access was unavailable.
- `EMB` is included as a non-traded price sensor, but the local run could not download it in the sandbox. Sensor hooks are active when EMB data is available.
- `VIXCLS`, `T10Y2Y`, and `BAMLEMCBPIOAS` are included in the public ALFRED/FRED macro config. Real ALFRED validation still requires `FRED_API_KEY` and network access.
