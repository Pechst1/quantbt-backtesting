# Walk-Forward Candidate EV Gate

CSV: `reports/market_wizards/global_macro_barbell_research_reversal_candidate_research.csv`
Horizon: `21` trading days
Top-N: `3`
EV hurdle: `0.0025`
Tail limit: `-0.1000`
Minimum bucket observations: `40`

## Summary

| index | count | mean | median | hit_rate | profit_factor | sum_return |
| --- | --- | --- | --- | --- | --- | --- |
| actual_selected | 4092.000000 | 0.017963 | 0.014883 | 0.605572 | 1.948557 | 73.506614 |
| actual_selected_ev_gate | 2370.000000 | 0.012856 | 0.015401 | 0.638397 | 1.879256 | 30.467930 |
| ev_rank_top_n | 4170.000000 | 0.011326 | 0.012889 | 0.626139 | 1.930929 | 47.230141 |

## EV Top-N By Symbol

| symbol | count | mean | median |
| --- | --- | --- | --- |
| EEM | 1261 | 0.016073 | 0.015768 |
| SPY | 1679 | 0.012619 | 0.016009 |
| DBC | 810 | 0.006566 | 0.008280 |
| UUP | 327 | 0.004648 | 0.001589 |
| USO | 93 | -0.011432 | -0.028725 |

## EV Top-N By Episode

| episode | count | mean | median |
| --- | --- | --- | --- |
| commodity_growth_2014_2016 | 841 | 0.000591 | 0.000793 |
| covid_2020 | 128 | 0.033434 | 0.040079 |
| inflation_hiking_2021_2022 | 1177 | 0.014313 | 0.012987 |
| late_cycle_2017_2019 | 922 | 0.012358 | 0.014221 |
| normalization_2023_2024 | 510 | 0.016663 | 0.021383 |
| post_gfc_qe_2010_2013 | 592 | 0.009654 | 0.013188 |

## Gate Pass Rate By Symbol

| symbol | pass_rate |
| --- | --- |
| SPY | 0.725270 |
| EEM | 0.544708 |
| DBC | 0.481127 |
| UUP | 0.289586 |
| USO | 0.071549 |

