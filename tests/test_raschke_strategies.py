from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quantbt.core.models import Bar
from quantbt.core.events import MarketEvent
from quantbt.data.base import DataSource
from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.raschke import HolyGrailStrategy, TurtleSoupPlusOneStrategy

FREE = ExecutionConfig(slippage_bps=0.0, spread_bps=0.0, commission_pct=0.0, sec_fee_rate=0.0, exchange_fee_per_share=0.0)


class FrameSource(DataSource):
    name = "raschke_synthetic"

    def __init__(self, frames):
        self.frames = frames

    def fetch_ohlcv(self, symbol, start, end, interval):
        return self.frames[symbol].copy()


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(rows))
    o, h, l, c = (np.array(col, dtype=float) for col in zip(*rows))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "adj_close": c, "volume": 1e6}, index=index)


def _run(frame: pd.DataFrame, cls, tmp_path, **kwargs):
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=["X"], start=frame.index[0].to_pydatetime(), end=frame.index[-1].to_pydatetime(), cache_dir=tmp_path),
        source=FrameSource({"X": frame}),
    )
    strategy = cls(symbols=data.active_symbols, **kwargs)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0)
    BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio, execution_handler=SimulatedExecutionHandler(FREE)).run()
    return strategy, portfolio


def _soup_base() -> list[tuple[float, float, float, float]]:
    rows = [(100, 101, 99, 100)] * 25
    rows[5] = (100, 101, 95, 100)  # the prior 20-day low, 20 sessions before day 1
    rows.append((96, 96, 93, 94))  # day 1: new low 93, close 94 <= 95
    return rows


def test_turtle_soup_plus_one_enters_at_prior_low_and_trails_the_stop(tmp_path):
    rows = _soup_base()
    rows.append((94, 97, 92, 96))  # day 2: buy stop at 95 fills; stop = min(93, 92) = 92
    rows.append((96, 98, 94.5, 97))  # stop trails to 94.5 at this close
    rows.append((96, 96, 94, 95))  # trailing stop at 94.5 is hit
    rows += [(95, 96, 94, 95)] * 3
    _, portfolio = _run(_frame(rows), TurtleSoupPlusOneStrategy, tmp_path)
    assert len(portfolio.closed_trades) == 1
    trade = portfolio.closed_trades[0]
    assert trade.entry_price == pytest.approx(95.0)
    assert trade.exit_price == pytest.approx(94.5)
    assert trade.exit_timestamp == pd.bdate_range("2020-01-01", periods=len(rows))[28]


def test_turtle_soup_buy_stop_is_good_for_one_day_only(tmp_path):
    rows = _soup_base()
    rows.append((94, 94.8, 92, 94))  # day 2 never trades back to 95: order expires
    rows += [(100, 101, 99, 100)] * 3  # would have filled a resting order
    _, portfolio = _run(_frame(rows), TurtleSoupPlusOneStrategy, tmp_path)
    assert not portfolio.closed_trades
    assert all(p.quantity == 0 for p in portfolio.positions.values())


def test_turtle_soup_needs_prior_low_at_least_three_sessions_old(tmp_path):
    rows = [(100, 101, 99, 100)] * 26
    rows[24] = (100, 101, 95, 100)  # prior low only two sessions before day 1
    rows.append((96, 96, 93, 94))
    rows += [(94, 97, 92, 96)] * 3
    strategy, portfolio = _run(_frame(rows), TurtleSoupPlusOneStrategy, tmp_path)
    trades = portfolio.closed_trades
    assert not trades or trades[0].entry_timestamp != _frame(rows).index[27]


def test_turtle_soup_time_exit_after_six_bars(tmp_path):
    rows = _soup_base()
    rows.append((94, 97, 92, 96))  # entry bar (index 26)
    rows += [(96 + k, 98 + k, 95 + k, 97 + k) for k in range(10)]  # steady rise, stop never hit
    _, portfolio = _run(_frame(rows), TurtleSoupPlusOneStrategy, tmp_path)
    trade = portfolio.closed_trades[0]
    index = _frame(rows).index
    assert trade.exit_timestamp == index[26 + 7]  # six bars held, sold at the next open
    assert trade.exit_price == pytest.approx(rows[33][0])


def _wilder_adx(high, low, close, n=14):
    tr = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])])
    up, down = high[1:] - high[:-1], low[:-1] - low[1:]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)

    def smooth(x):
        out = np.full(len(x), np.nan)
        out[n - 1] = x[:n].sum()
        for k in range(n, len(x)):
            out[k] = out[k - 1] - out[k - 1] / n + x[k]
        return out

    str_, spdm, smdm = smooth(tr), smooth(pdm), smooth(mdm)
    pdi, mdi = 100 * spdm / str_, 100 * smdm / str_
    dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = np.full(len(dx), np.nan)
    first = n - 1
    adx[first + n - 1] = dx[first : first + n].mean()
    for k in range(first + n, len(dx)):
        adx[k] = (adx[k - 1] * (n - 1) + dx[k]) / n
    return adx[-1], pdi[-1], mdi[-1]


def test_holy_grail_adx_matches_wilder_reference():
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 120)))
    high = close * (1 + rng.uniform(0, 0.02, 120))
    low = close * (1 - rng.uniform(0, 0.02, 120))
    strategy = HolyGrailStrategy(symbols=["X"])
    for k in range(120):
        ts = datetime(2020, 1, 1) + pd.Timedelta(days=k)
        bar = Bar("X", ts, close[k], high[k], low[k], close[k], 1e6)
        strategy.on_data(MarketEvent(timestamp=ts, bars={"X": bar}))
    adx, pdi, mdi = _wilder_adx(high, low, close)
    assert strategy._adx[0] == pytest.approx(adx)
    assert strategy._plus_di[0] == pytest.approx(pdi)
    assert strategy._minus_di[0] == pytest.approx(mdi)


def test_holy_grail_buys_pullback_to_ema_and_exits_at_swing_high(tmp_path):
    rows = [(100 + 1.5 * k, 101 + 1.5 * k, 99.5 + 1.5 * k, 100.5 + 1.5 * k) for k in range(80)]  # strong trend
    top = rows[-1][1]  # swing high
    price = rows[-1][3]
    for _ in range(6):  # pullback toward the 20 EMA
        price -= 3.0
        rows.append((price + 1, price + 1.5, price - 0.5, price))
    rows += [(price + 2, price + 4, price + 1, price + 3)]  # bounce: should take out the setup high
    rows += [(price + 4 + 3 * k, price + 6 + 3 * k, price + 3 + 3 * k, price + 5 + 3 * k) for k in range(12)]
    frame = _frame(rows)
    strategy, portfolio = _run(frame, HolyGrailStrategy, tmp_path)
    assert portfolio.closed_trades, "expected a Holy Grail trade"
    trade = portfolio.closed_trades[0]
    # Sell limit at the swing high; a gap above it fills at the open.
    assert trade.exit_price >= top
    assert trade.metadata.get("exit_reason") == "TARGET", trade.metadata
    # Entry was a buy stop at the high of a bar whose low touched the EMA.
    assert trade.entry_price in {r[1] for r in rows} or trade.entry_price in {r[0] for r in rows}
