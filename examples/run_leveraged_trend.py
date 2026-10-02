"""
Leveraged SPY/QQQ with Paul Tudor Jones's 200-day filter versus buy-and-hold.

Runs exactly the variants pre-declared in reports/leveraged_trend/PREREGISTRATION.md
through the QuantBT engine. Run examples/fetch_sp500_panel.py first.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.data.leveraged import FrameSource, load_fred_rate, rate_lookup, synthetic_leveraged_ohlc, total_return_ohlc
from quantbt.data.panel import ParquetPanelSource
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.trend_filter import TrendFilteredHoldStrategy

UNDERLYINGS = ("SPY", "QQQ")
LEVERAGES = (1, 2, 3)
HEADLINE_SMA = 200
ROBUSTNESS_SMAS = (100, 150, 250)  # 3x only, reported but never used to pick a variant
PERIODS = {"in_sample_1999_2019": ("1999-01-01", "2019-12-31"), "holdout_2020_2026": ("2020-01-01", "2026-09-30")}
TBILL_ETF_FEE = 0.001


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default=".cache/sp500_panel/prices.parquet")
    parser.add_argument("--data-start", default="1997-06-01", help="Earlier than scoring so the 200-day average is warm.")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output-dir", default="reports/leveraged_trend")
    return parser.parse_args()


def stats(equity: pd.Series, rf: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    days = equity.index.to_series().diff().dt.days.loc[rets.index]
    excess = rets - rf.reindex(rets.index, method="ffill").fillna(0.0) * days / 365.0
    dd = equity / equity.cummax() - 1.0
    underwater = (dd < 0).astype(int)
    longest = int(underwater.groupby((underwater == 0).cumsum()).sum().max())
    return {
        "cagr": float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1),
        "ann_vol": float(rets.std() * np.sqrt(252)),
        "sharpe": float(excess.mean() / excess.std() * np.sqrt(252)),
        "max_drawdown": float(dd.min()),
        "longest_drawdown_days": longest,
        "growth_of_1": float(equity.iloc[-1] / equity.iloc[0]),
    }


def run_variant(underlying: str, lev: int, sma: int | None, frames: dict[str, pd.DataFrame], rate: pd.Series,
                start: str, end: str) -> tuple[pd.Series, pd.Series, dict]:
    trade = underlying if lev == 1 else f"{underlying}_{lev}X"
    source_frames = {underlying: frames[underlying]}
    if lev > 1:
        source_frames[trade] = frames[trade]
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=list(source_frames), start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
                          cache_dir=Path(".cache/market_data"), adjust_prices=False),
        source=FrameSource(source_frames),
    )
    strategy = TrendFilteredHoldStrategy(signal_symbol=underlying, trade_symbol=trade, sma_window=sma)
    portfolio = Portfolio(
        initial_cash=100_000.0,
        leverage=1.0,
        cash_interest_rate=rate_lookup(rate, -TBILL_ETF_FEE),
        margin_interest_rate=rate_lookup(rate, 0.005),  # only touched when an open gap overshoots cash
    )
    engine = BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio,
                            execution_handler=SimulatedExecutionHandler(ExecutionConfig()))
    result = engine.run(run_name=f"{trade}_{sma or 'bh'}")
    equity = result.report.equity_curve
    invested = pd.Series([s.gross_exposure / s.equity > 0.5 for s in portfolio.history],
                         index=[s.timestamp for s in portfolio.history], dtype=float)
    info = {"switches": strategy.switches, "fills": result.fills, "margin_calls": result.margin_calls,
            "cash_interest": round(result.cash_interest_earned, 2), "margin_interest": round(result.margin_interest_paid, 2)}
    return equity, invested, info


def plot_headline(equity: pd.DataFrame, windows: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(UNDERLYINGS), figsize=(14, 5), sharey=True)
    for ax, u in zip(axes, UNDERLYINGS):
        start = windows[u]["full"][0]
        for lev in LEVERAGES:
            for sma, style in ((None, ":"), (HEADLINE_SMA, "-")):
                name = f"{u} {lev}x {'buy&hold' if sma is None else f'SMA{sma}'}"
                seg = equity.loc[start:, name]
                ax.plot(seg / seg.iloc[0], style, label=name, color=f"C{lev - 1}", linewidth=1)
        spy = equity.loc[start:, "SPY 1x buy&hold"]
        ax.plot(spy / spy.iloc[0], color="black", linewidth=1.2, label="SPY buy&hold")
        ax.axvline(pd.Timestamp("2020-01-01"), color="grey", linewidth=0.8)
        ax.set_yscale("log")
        ax.set_title(f"{u}: growth of $1 (log), hold-out right of grey line")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "equity_curves.png", dpi=110)


def main() -> None:
    args = parse_args()
    rate = load_fred_rate("DTB3")
    panel = ParquetPanelSource(args.prices, symbols=list(UNDERLYINGS))
    frames: dict[str, pd.DataFrame] = {}
    for u in UNDERLYINGS:
        raw = panel.fetch_ohlcv(u, datetime.fromisoformat(args.data_start), datetime.fromisoformat(args.end), "1d")
        frames[u] = total_return_ohlc(raw)
        for lev in LEVERAGES[1:]:
            frames[f"{u}_{lev}X"] = synthetic_leveraged_ohlc(raw, lev, rate)

    variants = [(u, lev, sma) for u in UNDERLYINGS for lev in LEVERAGES for sma in (None, HEADLINE_SMA)]
    variants += [(u, 3, sma) for u in UNDERLYINGS for sma in ROBUSTNESS_SMAS]

    curves, exposure, infos = {}, {}, {}
    for u, lev, sma in variants:
        name = f"{u} {lev}x {'buy&hold' if sma is None else f'SMA{sma}'}"
        print(f"running {name}")
        curves[name], exposure[name], infos[name] = run_variant(u, lev, sma, frames, rate, args.data_start, args.end)

    # Each underlying is scored from the later of 1999-01-01 and the first day its 200-day
    # average exists, so buy-and-hold and filtered variants start on the same day. QQQ only
    # trades from 1999-03-10, so its windows start in late 1999.
    equity = pd.DataFrame(curves).ffill()
    windows: dict[str, dict[str, tuple[str, str]]] = {}
    for u in UNDERLYINGS:
        live = str(max(frames[u].index[HEADLINE_SMA], pd.Timestamp(PERIODS["in_sample_1999_2019"][0])).date())
        windows[u] = {"full": (live, args.end)} | {k: (max(lo, live), hi) for k, (lo, hi) in PERIODS.items()}

    results = {}
    for name in curves:
        u = name.split()[0]
        row = {"robustness_only": any(f"SMA{s}" in name for s in ROBUSTNESS_SMAS), **infos[name]}
        for w, (lo, hi) in windows[u].items():
            seg = equity[name].loc[lo:hi]
            row[w] = stats(seg, rate) | {"time_invested": float(exposure[name].loc[lo:hi].mean())}
        results[name] = row

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"windows": windows, "variants": results}, indent=2))
    for u in UNDERLYINGS:
        cols = [c for c in equity if c.startswith(u)]
        seg = equity.loc[windows[u]["full"][0]:, cols]
        (seg / seg.iloc[0]).to_csv(out / f"equity_curves_{u}.csv", float_format="%.5f")

    plot_headline(equity, windows, out)

    for w in ("full", *PERIODS):
        print(f"\n== {w} {[windows[u][w] for u in UNDERLYINGS]}")
        print(f"{'variant':24s} {'CAGR':>7s} {'Sharpe':>7s} {'MaxDD':>7s} {'Vol':>6s} {'x':>8s}")
        for name, row in results.items():
            s = row[w]
            print(f"{name:24s} {s['cagr']:7.1%} {s['sharpe']:7.2f} {s['max_drawdown']:7.1%} {s['ann_vol']:6.1%} {s['growth_of_1']:8.1f}")


if __name__ == "__main__":
    main()
