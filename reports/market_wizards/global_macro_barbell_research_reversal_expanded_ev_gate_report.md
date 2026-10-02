# Walk-Forward Candidate EV Gate

CSV: `reports/market_wizards/global_macro_barbell_research_reversal_expanded_candidate_research.csv`
Horizon: `21` trading days
Top-N: `3`
EV hurdle: `0.0025`
Tail limit: `-0.1000`
Minimum bucket observations: `40`

## Summary

| index | count | mean | median | hit_rate | profit_factor | sum_return |
| --- | --- | --- | --- | --- | --- | --- |
| actual_selected | 4344.000000 | 0.014065 | 0.009717 | 0.580571 | 1.900426 | 61.097653 |
| actual_selected_ev_gate | 2275.000000 | 0.010982 | 0.011970 | 0.613626 | 1.792689 | 24.985086 |
| ev_rank_top_n | 5975.000000 | 0.009588 | 0.009324 | 0.606527 | 1.848200 | 57.290947 |

## EV Top-N By Symbol

| symbol | count | mean | median |
| --- | --- | --- | --- |
| EEM | 1256 | 0.015903 | 0.015659 |
| SPY | 1491 | 0.013958 | 0.017017 |
| DBC | 766 | 0.007620 | 0.009598 |
| TLT | 1071 | 0.007212 | 0.004705 |
| IEF | 752 | 0.007211 | 0.005393 |
| UUP | 358 | -0.000366 | -0.002638 |
| GLD | 151 | -0.006079 | -0.005151 |
| TIP | 37 | -0.009890 | -0.015007 |
| USO | 93 | -0.011432 | -0.028725 |

## EV Top-N By Episode

| episode | count | mean | median |
| --- | --- | --- | --- |
| commodity_growth_2014_2016 | 1415 | 0.001752 | 0.001762 |
| covid_2020 | 226 | 0.026006 | 0.029131 |
| inflation_hiking_2021_2022 | 1257 | 0.013048 | 0.011291 |
| late_cycle_2017_2019 | 1011 | 0.011337 | 0.012446 |
| normalization_2023_2024 | 765 | 0.011290 | 0.015478 |
| post_gfc_qe_2010_2013 | 1301 | 0.009557 | 0.009414 |

## Gate Pass Rate By Symbol

| symbol | pass_rate |
| --- | --- |
| SPY | 0.702376 |
| TIP | 0.625963 |
| TLT | 0.609954 |
| EEM | 0.544708 |
| IEF | 0.491620 |
| DBC | 0.481127 |
| UUP | 0.289586 |
| GLD | 0.245869 |
| USO | 0.071549 |

