# Macro Barbell Experiment Analysis

## Variant Comparison

| Variant | CAGR | Sharpe | MaxDD | PF | Phase3 Ratio | Overlay Weight |
|---|---:|---:|---:|---:|---:|---:|
| step_d_concentration | 12.1141% | 0.8807 | -23.9935% | 4.3928 | 3.0797% | 0.2023% |
| phase3_profit_rate | 12.1636% | 0.8839 | -23.9949% | 4.4139 | 1.3113% | 0.2023% |
| overlay_routing | 12.2091% | 0.8881 | -23.9980% | 4.5340 | 1.3113% | 0.0000% |
| focused_reallocation | 12.1636% | 0.8839 | -23.9949% | 4.4139 | 1.3113% | 0.2023% |

## step_d_concentration

- `pre_crisis_2005_2007`: Engine B PnL `0.00`, trades `0`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `none`
- `gfc_2007_2009`: Engine B PnL `213413.98`, trades `98`, Phase 3 trades `11`, Phase 3 PnL fraction `39.92%`, top instruments `USO (100915.87), DBC (47841.85), SPY (42423.02)`
- `post_gfc_qe_2010_2013`: Engine B PnL `90598.05`, trades `86`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59766.37), SPY (30831.68)`
- `commodity_growth_2014_2016`: Engine B PnL `289975.25`, trades `90`, Phase 3 trades `4`, Phase 3 PnL fraction `25.50%`, top instruments `USO (123578.08), DBC (110927.94), EEM (41066.70)`
- `late_cycle_2017_2019`: Engine B PnL `91343.08`, trades `64`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59957.09), USO (32529.96), DBC (8691.90)`
- `covid_2020`: Engine B PnL `529884.07`, trades `35`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `USO (370075.83), DBC (83920.12), SPY (44039.52)`
- `inflation_hiking_2021_2022`: Engine B PnL `814219.36`, trades `46`, Phase 3 trades `20`, Phase 3 PnL fraction `75.66%`, top instruments `DBC (304941.39), EEM (284236.51), USO (144826.34)`
- `normalization_2023_2024`: Engine B PnL `70497.33`, trades `38`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `SPY (52391.78), EEM (18105.55)`

## phase3_profit_rate

- `pre_crisis_2005_2007`: Engine B PnL `0.00`, trades `0`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `none`
- `gfc_2007_2009`: Engine B PnL `213435.19`, trades `98`, Phase 3 trades `12`, Phase 3 PnL fraction `49.41%`, top instruments `USO (100908.19), DBC (47850.70), SPY (42410.17)`
- `post_gfc_qe_2010_2013`: Engine B PnL `90603.90`, trades `86`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59776.90), SPY (30827.00)`
- `commodity_growth_2014_2016`: Engine B PnL `289986.13`, trades `90`, Phase 3 trades `4`, Phase 3 PnL fraction `60.68%`, top instruments `USO (123578.85), DBC (110940.11), EEM (41064.64)`
- `late_cycle_2017_2019`: Engine B PnL `91337.85`, trades `64`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59955.38), USO (32526.96), DBC (8691.37)`
- `covid_2020`: Engine B PnL `529897.29`, trades `35`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `USO (370079.62), DBC (83926.07), SPY (44039.52)`
- `inflation_hiking_2021_2022`: Engine B PnL `834635.51`, trades `45`, Phase 3 trades `21`, Phase 3 PnL fraction `76.02%`, top instruments `DBC (317998.74), EEM (292152.30), USO (144834.36)`
- `normalization_2023_2024`: Engine B PnL `71157.13`, trades `38`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `SPY (52892.15), EEM (18264.98)`

## overlay_routing

- `pre_crisis_2005_2007`: Engine B PnL `0.00`, trades `0`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `none`
- `gfc_2007_2009`: Engine B PnL `212608.50`, trades `96`, Phase 3 trades `12`, Phase 3 PnL fraction `49.28%`, top instruments `USO (100222.33), DBC (47973.88), SPY (41881.98)`
- `post_gfc_qe_2010_2013`: Engine B PnL `89728.88`, trades `74`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (58325.28), SPY (31403.60)`
- `commodity_growth_2014_2016`: Engine B PnL `295383.76`, trades `84`, Phase 3 trades `4`, Phase 3 PnL fraction `59.53%`, top instruments `USO (123722.85), DBC (111853.50), EEM (41256.65)`
- `late_cycle_2017_2019`: Engine B PnL `91694.61`, trades `59`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (60820.48), USO (32849.18), DBC (8361.67)`
- `covid_2020`: Engine B PnL `533314.04`, trades `36`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `USO (372278.31), DBC (84409.64), SPY (44558.85)`
- `inflation_hiking_2021_2022`: Engine B PnL `840698.91`, trades `44`, Phase 3 trades `21`, Phase 3 PnL fraction `76.15%`, top instruments `DBC (319977.87), EEM (295759.72), USO (145712.53)`
- `normalization_2023_2024`: Engine B PnL `74143.26`, trades `28`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `SPY (50300.15), EEM (23843.11)`

## focused_reallocation

- `pre_crisis_2005_2007`: Engine B PnL `0.00`, trades `0`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `none`
- `gfc_2007_2009`: Engine B PnL `213435.19`, trades `98`, Phase 3 trades `12`, Phase 3 PnL fraction `49.41%`, top instruments `USO (100908.19), DBC (47850.70), SPY (42410.17)`
- `post_gfc_qe_2010_2013`: Engine B PnL `90603.90`, trades `86`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59776.90), SPY (30827.00)`
- `commodity_growth_2014_2016`: Engine B PnL `289986.13`, trades `90`, Phase 3 trades `4`, Phase 3 PnL fraction `60.68%`, top instruments `USO (123578.85), DBC (110940.11), EEM (41064.64)`
- `late_cycle_2017_2019`: Engine B PnL `91337.85`, trades `64`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `EEM (59955.38), USO (32526.96), DBC (8691.37)`
- `covid_2020`: Engine B PnL `529897.29`, trades `35`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `USO (370079.62), DBC (83926.07), SPY (44039.52)`
- `inflation_hiking_2021_2022`: Engine B PnL `834635.51`, trades `45`, Phase 3 trades `21`, Phase 3 PnL fraction `76.02%`, top instruments `DBC (317998.74), EEM (292152.30), USO (144834.36)`
- `normalization_2023_2024`: Engine B PnL `71157.13`, trades `38`, Phase 3 trades `0`, Phase 3 PnL fraction `0.00%`, top instruments `SPY (52892.15), EEM (18264.98)`
