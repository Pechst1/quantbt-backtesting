from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.kullamaggie import KullamaggieStrategy

FREE = ExecutionConfig(slippage_bps=0.0, spread_bps=0.0, commission_pct=0.0, sec_fee_rate=0.0, exchange_fee_per_share=0.0)


class FrameSource(DataSource):
    name = "kullamaggie_synthetic"

    def __init__(self, frames):
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(rows) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(rows))
    o, h, l, c, v = (np.array(col, dtype=float) for col in zip(*rows))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "adj_close": c, "volume": v}, index=index)


def _run(frame, tmp_path, **kwargs):
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=["X"], start=frame.index[0].to_pydatetime(), end=frame.index[-1].to_pydatetime(), cache_dir=tmp_path),
        source=FrameSource({"X": frame}),
    )
    strategy = KullamaggieStrategy(symbols=data.active_symbols, market_symbol=None, **kwargs)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0)
    BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(FREE)).run()
    return strategy, portfolio


def _gap_rows(gap_volume=5e6):
    rows = [(100, 101, 99, 100, 1e6)] * 25
    rows.append((112, 116, 112, 115, gap_volume))  # 12% gap, held, range 3 <= 1.5 ADR
    return rows


def test_episodic_pivot_buys_next_open_sells_a_third_then_stops_at_entry(tmp_path):
    rows = _gap_rows()
    rows.append((115, 117, 114, 116, 2e6))  # entry at the open (115); stop = gap-day low 112
    rows += [(116, 118, 115.5, 117, 2e6), (117, 119, 116.5, 118, 2e6), (118, 120, 117.5, 119, 2e6)]
    rows.append((119, 119, 114, 114.5, 2e6))  # third sold at 119; rest stopped at entry 115
    rows += [(114, 115, 113, 114, 1e6)] * 2
    _, portfolio = _run(_frame(rows), tmp_path, setup="ep")
    trades = portfolio.closed_trades
    shares = int(min(1_000 / 3.0, 25_000 / 115.0))  # 1% risk vs 25% cap -> cap binds
    assert sum(t.quantity for t in trades) == shares
    exits = sorted((t.exit_price, t.quantity) for t in trades)
    assert exits[0] == (pytest.approx(115.0), shares - shares // 3)
    assert exits[1] == (pytest.approx(119.0), shares // 3)
    assert all(t.entry_price == pytest.approx(115.0) for t in trades)


def test_episodic_pivot_needs_heavy_volume(tmp_path):
    rows = _gap_rows(gap_volume=2e6) + [(115, 117, 114, 116, 2e6)] * 3
    strategy, portfolio = _run(_frame(rows), tmp_path, setup="ep")
    assert strategy.setups_seen == 0
    assert not portfolio.closed_trades


def test_breakout_buys_pivot_with_stop_order_and_trails_sma(tmp_path):
    # 130 bars of a steady climb, then a tight 5-bar flag under a pivot, then the breakout.
    rows = []
    price = 50.0
    for _ in range(130):
        rows.append((price, price * 1.03, price * 0.99, price * 1.01, 1e6))
        price *= 1.01
    top = price * 1.05
    rows.append((price, top, price * 0.995, price * 1.02, 1e6))  # pivot bar
    base = price * 1.035
    for _ in range(4):
        rows.append((base, base * 1.01, base * 0.985, base, 1e6))
    rows.append((base, top * 1.02, base * 0.99, top * 1.01, 1e6))  # breaks the pivot
    for _ in range(5):
        rows.append((top, top * 1.02, top * 0.99, top * 1.01, 1e6))
    rows += [(top * 0.8, top * 0.8, top * 0.7, top * 0.75, 1e6)] * 2
    strategy, portfolio = _run(_frame(rows), tmp_path, setup="breakout", min_adr=0.03, top_fraction=1.0, min_pool=1)
    assert strategy.setups_seen >= 1
    trades = portfolio.closed_trades
    assert trades
    assert all(t.entry_price == pytest.approx(top) for t in trades)
    assert all(p.quantity == 0 for p in portfolio.positions.values())
