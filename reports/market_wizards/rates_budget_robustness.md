# Rates Budget Robustness

## Variant Comparison

| Variant | CAGR | Sharpe | MaxDD | Calmar | PF | Trades | Rates PnL | Rates PF | Rates Gross |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short095_long075 | 16.61% | 1.122 | -19.79% | 0.839 | 5.683 | 743 | 1,883,032 | 10.237 | 5.68% |
| short095_long125 | 16.62% | 1.123 | -19.82% | 0.839 | 5.689 | 722 | 1,886,961 | 10.321 | 5.38% |
| short105_long035 | 16.22% | 1.108 | -19.79% | 0.820 | 5.500 | 731 | 1,658,743 | 9.501 | 5.33% |

## short095_long075 Episode Attribution

| Episode | Total PnL | Rates PnL | Rates PF | Top Rates Symbols |
|---|---:|---:|---:|---|
| all_other_periods | 1,398,248 | 497,874 | 5.839 | 2YY=F 293,012, ^FVX 156,774, ^TNX 47,889 |
| crisis_2007_2009 | 272,566 | 17,663 | 3.249 | ^TNX 11,494, ^FVX 4,514, IEF 1,696 |
| shock_2020 | 957,846 | 158,636 | inf | ^TNX 92,205, ^FVX 44,597, TLT 10,724 |
| shock_2022 | 2,112,286 | 1,208,055 | 18.416 | 2YY=F 772,302, ^TNX 435,753 |
| transition_2014_2016 | 317,847 | 804 | 1.034 | ^TNX 11,992, TLT 1,963, IEF 1,635 |

## short095_long125 Episode Attribution

| Episode | Total PnL | Rates PnL | Rates PF | Top Rates Symbols |
|---|---:|---:|---:|---|
| all_other_periods | 1,400,289 | 498,499 | 5.844 | 2YY=F 293,279, ^FVX 157,004, ^TNX 48,016 |
| crisis_2007_2009 | 272,256 | 17,618 | 3.244 | ^TNX 11,494, ^FVX 4,514, IEF 1,696 |
| shock_2020 | 959,055 | 158,799 | inf | ^TNX 92,367, ^FVX 44,583, TLT 10,723 |
| shock_2022 | 2,114,954 | 1,209,867 | 18.373 | 2YY=F 773,547, ^TNX 436,320 |
| transition_2014_2016 | 319,234 | 2,178 | 1.099 | ^TNX 11,856, TLT 3,224, IEF 1,851 |

## short105_long035 Episode Attribution

| Episode | Total PnL | Rates PnL | Rates PF | Top Rates Symbols |
|---|---:|---:|---:|---|
| all_other_periods | 1,325,180 | 459,390 | 5.773 | 2YY=F 271,882, ^FVX 143,894, ^TNX 43,414 |
| crisis_2007_2009 | 272,566 | 17,663 | 3.249 | ^TNX 11,494, ^FVX 4,514, IEF 1,696 |
| shock_2020 | 802,381 | 19,690 | 7.423 | TLT 10,604, IEF 9,750, ZN=F 1,266 |
| shock_2022 | 1,996,153 | 1,161,196 | 19.082 | 2YY=F 712,157, ^TNX 449,039 |
| transition_2014_2016 | 317,847 | 804 | 1.034 | ^TNX 11,992, TLT 1,963, IEF 1,635 |
