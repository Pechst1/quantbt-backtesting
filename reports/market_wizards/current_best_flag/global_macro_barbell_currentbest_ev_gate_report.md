# Walk-Forward Candidate EV Gate

CSV: `reports/market_wizards/current_best_flag/global_macro_barbell_currentbest_candidate_research.csv`
Horizon: `21` trading days
Top-N: `3`
EV hurdle: `0.0025`
Tail limit: `-0.1000`
Minimum bucket observations: `40`

## Summary

| index | count | mean | median | hit_rate | profit_factor | sum_return |
| --- | --- | --- | --- | --- | --- | --- |
| actual_selected | 6183.000000 | 0.033128 | 0.016709 | 0.622190 | 2.820069 | 204.831568 |
| actual_selected_ev_gate | 3140.000000 | 0.016083 | 0.012848 | 0.635987 | 2.164733 | 50.501629 |
| ev_rank_top_n | 4716.000000 | 0.013154 | 0.011588 | 0.625318 | 2.083717 | 62.035205 |

## EV Top-N By Symbol

| symbol | count | mean | median |
| --- | --- | --- | --- |
| ^TNX | 224 | 0.069615 | 0.077020 |
| EEM | 1277 | 0.014945 | 0.015049 |
| SPY | 1532 | 0.011808 | 0.015586 |
| IEF | 146 | 0.009939 | 0.007940 |
| DBC | 781 | 0.007134 | 0.009369 |
| TLT | 214 | 0.005775 | 0.001077 |
| ZN=F | 116 | 0.005312 | 0.001012 |
| UUP | 333 | 0.004368 | 0.001217 |
| USO | 93 | -0.011432 | -0.028725 |

## EV Top-N By Episode

| episode | count | mean | median |
| --- | --- | --- | --- |
| commodity_growth_2014_2016 | 977 | 0.001349 | 0.001762 |
| covid_2020 | 231 | 0.021738 | 0.025197 |
| inflation_hiking_2021_2022 | 1229 | 0.021992 | 0.013713 |
| late_cycle_2017_2019 | 1177 | 0.012280 | 0.011436 |
| normalization_2023_2024 | 510 | 0.016663 | 0.021383 |
| post_gfc_qe_2010_2013 | 592 | 0.009654 | 0.013188 |

## Gate Pass Rate By Symbol

| symbol | pass_rate |
| --- | --- |
| IEF | 1.000000 |
| ZN=F | 1.000000 |
| TLT | 0.779359 |
| SPY | 0.732181 |
| EEM | 0.551620 |
| DBC | 0.481127 |
| ^TNX | 0.331361 |
| UUP | 0.289586 |
| USO | 0.071549 |
| 2YY=F | 0.003185 |
| ^FVX | 0.000000 |

