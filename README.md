# QuantBT: Event-Driven Backtesting Engine

Modulare, professionell strukturierte Backtesting-Umgebung mit Fokus auf realistische Ausfuehrungssimulation und einfache Strategy-Entwicklung.

## Kernprinzipien

- Event-Driven Loop (`MARKET -> SIGNAL -> ORDER -> FILL`) statt rein vektorisierter Pipeline
- Oeffentliche Datenquellen (Yahoo Finance) mit lokalem Parquet-Caching
- Multi-Asset-Unterstuetzung
- Realistische Ausfuehrung mit Slippage, Spread und Kommissionen
- Portfolio-/Margin-Management und laufendes Mark-to-Market
- Tear-Sheet-Reporting inklusive Equity, Drawdown und Monats-Heatmap

## Architektur

```text
quantbt/
  core/        Event- und Domaintypen (Bar, Signal, Order, Fill, Position)
  data/        Datenquelle, Caching, Cleaning, Multi-Asset-Alignment
  altdata/     Point-in-Time Earnings/Fundamentals/CorporateActions/Borrow/Factors
  execution/   Order-Matching + Kostenmodell + OCO/Bracket-Orders
  portfolio/   Positionen, Cash, PnL, Margin, Borrow Fees, Exposure Limits, Stop-Loss
  strategy/    Einfaches Strategy-Interface (nur on_data ueberschreiben)
  analytics/   Kennzahlen + Tear-Sheet + Walk-Forward-Optimierung
  engine.py    Event-Loop und Orchestrierung
  cli.py       Einstiegspunkt
```

## Installation

```bash
pip install -e .
```

Fuer Tests:

```bash
pip install -e ".[dev]"
```

## Schnellstart

```bash
quantbt --symbols AAPL,MSFT,SPY --start 2018-01-01 --end 2025-01-01 --interval 1d
```

Der Lauf erzeugt:

- JSON-Metriken
- CSV der Equity-Kurve
- PNG Tear-Sheet (Equity, Drawdown, Monthly Heatmap)

Standard-Pfad: `reports/`

## Beispiel: Strategy Interface

Strategy-Autoren muessen nur `on_data(self, market_event)` implementieren und `SignalEvent`s zurueckgeben.

Siehe:

- `src/quantbt/strategy/base.py`
- `src/quantbt/strategies/sma_cross.py`
- `src/quantbt/strategies/popular.py`
- `src/quantbt/strategies/behavioral.py`

## Enthaltene Strategien (14)

- SMA Crossover (20/100)
- EMA Crossover (12/26)
- RSI Mean Reversion
- Bollinger Mean Reversion
- MACD Signal Crossover
- Stochastic Oscillator Crossover
- CCI Mean Reversion
- Momentum 12-1
- ATR Breakout
- Turtle Breakout (55/20)
- Z-Score Mean Reversion
- PEAD Inconsistency-Avoidance (SUE-basiert)
- Anti-Lottery Short (Idiosynkratische Volatilitaet + Fundamentals)
- Capitulation Buy (3d Crash + Panic Volume + RSI)

## Point-in-Time Datenquellen

Implementierte Adapter (CSV + Public Proxies):

- Earnings (IBES/FactSet/Zacks-Style): `IBESEarningsCSVSource`, `CSVEarningsDataSource`, `YahooEarningsDataSource`
- Fundamentals (Compustat-Style): `CompustatFundamentalsCSVSource`, `CSVFundamentalsDataSource`
- Corporate Actions (CRSP-Style): `CRSPCorporateActionsCSVSource`, `CSVCorporateActionsDataSource`, `YahooCorporateActionsDataSource`
- Borrow/Short Data (Markit-Style): `MarkitBorrowCSVSource`, `CSVBorrowDataSource`, `StaticBorrowDataSource`
- Fama-French Faktoren: `CSVFactorDataSource`, `KenFrenchFactorDataSource`
- Security Master (Survivorship Bias Mitigation): `CSVSecurityMasterDataSource`
- Point-in-Time Index Membership: `CSVIndexMembershipDataSource`
- Dedicated universe builders: `HistoricalUniverseBuilder.sp500()`, `.sp1500()`, `.russell2000()`
- Event-driven macro snapshots from public factor releases: `build_public_macro_snapshot_frame()`, `write_public_macro_snapshot_csv()`

CSV-Templates:

- `examples/data_templates/earnings_pit_template.csv`
- `examples/data_templates/fundamentals_pit_template.csv`
- `examples/data_templates/corporate_actions_template.csv`
- `examples/data_templates/borrow_template.csv`
- `examples/data_templates/ff3_factors_template.csv`
- `examples/data_templates/security_master_template.csv`
- `examples/data_templates/index_membership_template.csv`
- `examples/data_templates/macro_factor_events_template.csv`

Wichtig:
- `index_membership_template.csv` ist nur ein kleines Schema-Beispiel (kein voller Index-Verlauf).
- Fuer echte PiT-Universe-Enforcement laedt man den Full-Datensatz:

```bash
python examples/load_index_membership_data.py
```

Output:
- `data/index_membership/sp500_membership_full.csv` (oeffentlicher S&P-500-Verlauf, Quelle: `fja05680/sp500`, 1996-heute)

## Point-in-Time Universe Enforcement

Du kannst Entries strikt auf historische Index-Mitgliedschaften begrenzen
(z. B. exakte S&P 500 / S&P 1500 / Russell 2000 Membership pro Datum):

```bash
python examples/run_behavioral_strategies.py \
  --strategy pead \
  --symbols AAPL,MSFT,NVDA,PLTR \
  --index-membership-csv data/index_membership/sp500_membership_full.csv \
  --universe sp500 \
  --start 2018-01-01 --end 2025-01-01 --interval 1d
```

Oder ohne expliziten CSV-Pfad (nutzt automatisch die Full-SP500-Datei, falls vorhanden):

```bash
python examples/run_behavioral_strategies.py \
  --strategy pead \
  --symbols AAPL,MSFT,NVDA,PLTR \
  --universe sp500 \
  --start 2018-01-01 --end 2025-01-01 --interval 1d
```

Hinweis:
- Enforcement blockiert neue Exposure außerhalb der PiT-Membership.
- Exit-Reduktionen bestehender Positionen bleiben erlaubt.
- Fuer `YahooEarningsDataSource` ist `lxml` erforderlich.

## Public Macro Dataset Builder

Der alte Makro-Pfad `data/market_wizards/macro_regimes.csv` ist nur eine glatte Proxy-Serie.
Fuer reichere Extreme-Regime-Aktivierung kann die Engine nun aus echten, oeffentlichen Faktor-Releases
ein event-getriebenes PiT-Makro-Dataset bauen.

Input-Schema:
- `region`
- `factor` in `growth,inflation,policy,liquidity,credit`
- `date`
- `release_date`
- `tradable_from` (optional)
- `value`
- `series` (optional)
- `source` (optional)
- `transform` in `level,diff_1,diff_12,pct_change_1,pct_change_12,zscore`
- `weight` (optional)

Builder ausfuehren:

```bash
python examples/build_public_macro_dataset.py \
  --events-csv examples/data_templates/macro_factor_events_template.csv \
  --output data/market_wizards/macro_regimes_public.csv
```

Oder direkt aus einer Public-Source-Konfiguration:

```bash
python examples/build_public_macro_from_config.py \
  --config-csv examples/data_templates/macro_public_series_template.csv \
  --events-output data/market_wizards/macro_factor_events_public.csv \
  --snapshots-output data/market_wizards/macro_regimes_public.csv
```

Hinweise:
- `macro_public_series_template.csv` ist bewusst konservativ und aktuell auf US-ALFRED-Serien ausgelegt.
- Fuer `ALFRED_API` bzw. `FRED_API` wird `FRED_API_KEY` oder `--fred-api-key` benoetigt.
- Wenn rohe Public-CSV-Dateien bereits vorliegen, kann derselbe Builder auch `LOCAL_CSV` oder `CSV_URL` verwenden.

Der Builder erzeugt:
- mehrere PiT-Snapshots pro Monat, falls Releases innerhalb des Monats eintreffen
- `snapshot_id` pro Makro-Update
- `quality_score` und `coverage_ratio`
- automatische `GLOBAL`-Aggregation aus den Regional-Snapshots

Wenn `data/market_wizards/macro_regimes_public.csv` existiert, nutzt
`examples/run_market_wizards_strategies.py` diese Datei automatisch. Alternativ:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --macro-csv data/market_wizards/macro_regimes_public.csv \
  --start 2005-01-01 --end 2025-01-01 --interval 1d --no-report
```

### Robust macro research variant

The legacy macro mapping is not robust to the public ALFRED/FRED dataset. Use the
market-evidence redesign for data-source-invariant research: market prices determine
direction, macro inputs remain diagnostic, and unconfirmed Engine B positions use
15% Phase-1 probes before scaling to full Phase-2/3 size.

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-robust-redesign \
  --macro-csv data/market_wizards/macro_regimes_public.csv \
  --start 2005-01-01 --end 2025-01-01 --interval 1d
```

This is a research candidate, not a production strategy. The validated 2005-2025
result is 6.42% CAGR, 0.52 Sharpe, -38.52% maximum drawdown, and 1.75 profit
factor. Identical results on legacy and public macro snapshots demonstrate data
source invariance, not sufficient economic quality.

### Official EIA WTI futures curve

The public-data pipeline can build a daily WTI term structure from the official
EIA NYMEX Contract 1-4 histories. The resulting signal uses Contract 1 versus
Contract 2 for one-month carry and Contract 1 versus Contract 4 for three-month
carry. It becomes tradable on the following business day and handles the negative
front-month WTI price in April 2020 without dropping the observation.

```bash
python examples/build_eia_wti_futures_curve.py --refresh
```

Use it in an explicit research ablation:

```bash
python examples/run_market_wizards_strategies.py \
  --strategy global_macro_barbell \
  --barbell-robust-redesign \
  --macro-csv data/market_wizards/macro_regimes_public.csv \
  --futures-curve-csv data/market_wizards/futures_curve_signals_eia_wti.csv \
  --barbell-commodity-futures-symbols CL=F,HG=F \
  --start 2005-01-01 --end 2025-01-01 --interval 1d
```

This layer is not part of the promoted baseline. After volatility-risk sizing it
returned 6.19% CAGR versus 6.42% without WTI, with a slightly worse drawdown. EIA
history currently ends on 2024-04-05, so the existing 10-day staleness guard stops
using the curve after that date.

## Behavioral Strategien ausfuehren

```bash
python examples/run_behavioral_strategies.py \
  --strategy pead \
  --symbols AAPL,MSFT,NVDA \
  --start 2018-01-01 --end 2025-01-01 --interval 1d
```

Beispiel Anti-Lottery Short mit PiT-Fundamentals:

```bash
python examples/run_behavioral_strategies.py \
  --strategy anti_lottery \
  --symbols IWM,SMCI,UPST,PLTR \
  --fundamentals-csv data/fundamentals_pit.csv \
  --factor-csv data/ff3_daily.csv \
  --start 2018-01-01 --end 2025-01-01 --interval 1d
```

## Minervini Trend Template auf historischen S&P-500-Mitgliedern

Die acht veroeffentlichten Kriterien aus Minervinis Trend Template, angewendet auf die
Point-in-Time-Mitglieder des S&P 500 inklusive vieler delisteter Titel.
Ergebnisse und Annahmen: `reports/minervini/README.md`.

```bash
python examples/fetch_sp500_panel.py              # Preise nach .cache/, Mitgliedschaft nach data/index_membership/
python examples/run_minervini_trend_template.py   # Kernvariante
python examples/run_minervini_trend_template.py --market-filter
python examples/sp500_panel_bias_check.py         # verbleibender Survivorship-Bias der Daten
```

## Strategie-Suite auf globalem 50-Jahres-Basket

Benchmark ueber einen großen globalen Equity-Basket (Nordamerika, Europa, Asien-Pazifik, LatAm).
Default-Zeitraum ist ca. 50 Jahre (`1975-01-01` bis `2025-01-01`).

```bash
python examples/run_strategy_suite.py --interval 1d
```

Hinweis:
- Nicht alle internationalen Ticker haben ueber den kompletten Zeitraum Daten.
- Die Suite ueberspringt Symbole ohne nutzbare Historie automatisch (`strict_symbols=False`).

## Unterstuetzte Ausfuehrungstypen

- `MARKET`
- `MOO` (Market on Open)
- `MOC` (Market on Close)
- `LIMIT`
- `STOP_LOSS`
- `TAKE_PROFIT`

Zusatzmodelle:

- Slippage (bps)
- Spread-Proxies (bps) + volumenbasierte Impact-Slippage
- Kommissionen (fix + prozentual) + SEC/Exchange Fees
- Shorting Constraints (Hard-to-Borrow Rejects)

## Wichtige Metriken im Tear-Sheet

- Sharpe Ratio
- Sortino Ratio
- Maximum Drawdown
- Win Rate
- Profit Factor
- Calmar Ratio
- Annualized Return
- Max Drawdown Duration (in Tagen)
