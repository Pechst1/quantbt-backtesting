from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.data.panel import ParquetPanelSource
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.minervini import MinerviniTrendTemplateStrategy


class FrameSource(DataSource):
    name = "minervini_synthetic"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(close: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "adj_close": close,
            "volume": 1_000_000.0,
        },
        index=index,
    )


def _run(frames: dict[str, pd.DataFrame], is_member, tmp_path, **kwargs):
    index = next(iter(frames.values())).index
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(
            symbols=list(frames),
            start=index[0].to_pydatetime(),
            end=index[-1].to_pydatetime(),
            cache_dir=tmp_path,
        ),
        source=FrameSource(frames),
    )
    strategy = MinerviniTrendTemplateStrategy(symbols=data.active_symbols, is_member=is_member, **kwargs)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0)
    BacktestEngine(
        data_handler=data,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=SimulatedExecutionHandler(ExecutionConfig()),
    ).run()
    return strategy, portfolio


def _universe(n_days: int = 400):
    index = pd.bdate_range("2020-01-01", periods=n_days)
    t = np.arange(n_days, dtype=float)
    leader = 50.0 * np.exp(0.002 * t)
    # Leader breaks down hard after day 330.
    leader[330:] = leader[329] * np.exp(-0.01 * np.arange(1, n_days - 329))
    flat = 50.0 + np.sin(t / 10.0)
    falling = 80.0 * np.exp(-0.001 * t)
    return index, {
        "LEAD": _frame(leader, index),
        "FLAT": _frame(flat, index),
        "DOWN": _frame(falling, index),
    }


def test_buys_only_template_leader_and_exits_on_breakdown(tmp_path) -> None:
    index, frames = _universe()
    strategy, portfolio = _run(frames, lambda s, d: True, tmp_path)

    traded = {t.symbol for t in portfolio.closed_trades}
    assert traded == {"LEAD"}
    trade = portfolio.closed_trades[0]
    # The template needs a full 52-week history before the first entry.
    assert trade.entry_timestamp >= index[252].to_pydatetime()
    assert trade.exit_timestamp > index[330].to_pydatetime()
    assert trade.metadata["exit_reason"] in {"BELOW_50DMA", "STOP_LOSS"}
    assert not any(p.quantity for p in portfolio.positions.values())


def test_non_members_are_never_bought(tmp_path) -> None:
    _, frames = _universe()
    _, portfolio = _run(frames, lambda s, d: s != "LEAD", tmp_path)
    assert portfolio.closed_trades == []
    assert not any(p.quantity for p in portfolio.positions.values())


def test_market_filter_blocks_entries_when_market_symbol_fails(tmp_path) -> None:
    _, frames = _universe()
    _, portfolio = _run(frames, lambda s, d: s != "DOWN", tmp_path, market_symbol="DOWN")
    assert portfolio.closed_trades == []


def test_parquet_panel_source_round_trip(tmp_path) -> None:
    index = pd.bdate_range("2021-01-01", periods=5)
    rows = []
    for ticker in ("AAA", "BBB-201901"):
        for i, ts in enumerate(index):
            rows.append(
                {"date": ts, "ticker": ticker, "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i,
                 "close": 1.5 + i, "volume": 100.0, "adj_close": 1.4 + i}
            )
    path = tmp_path / "prices.parquet"
    pd.DataFrame(rows).to_parquet(path)

    source = ParquetPanelSource(path, symbols=["BBB-201901"])
    assert source.symbols == ["BBB-201901"]
    frame = source.fetch_ohlcv("BBB-201901", datetime(2021, 1, 4), datetime(2021, 1, 6), "1d")
    assert list(frame.index) == list(index[1:4])
    assert frame["adj_close"].iloc[0] == 2.4
