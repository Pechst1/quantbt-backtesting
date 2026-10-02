from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quantbt.data.handler import DataHandlerConfig, PublicOHLCVDataHandler
from quantbt.data.leveraged import FrameSource, rate_lookup, synthetic_leveraged_ohlc
from quantbt.engine import BacktestEngine
from quantbt.execution.handler import ExecutionConfig, SimulatedExecutionHandler
from quantbt.portfolio.portfolio import Portfolio
from quantbt.strategies.trend_filter import TrendFilteredHoldStrategy


def _frame(close: np.ndarray, index: pd.DatetimeIndex, dividend_factor: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "adj_close": close * dividend_factor, "volume": 1e6},
        index=index,
    )


def test_synthetic_fund_compounds_leveraged_daily_returns_and_charges_carry() -> None:
    index = pd.bdate_range("2020-01-06", periods=4)  # Mon..Thu, one calendar day apart
    under = _frame(np.array([100.0, 110.0, 99.0, 99.0]), index)
    rate = pd.Series(0.0365, index=index)
    out = synthetic_leveraged_ohlc(under, 3, rate, expense_ratio=0.0365, financing_spread=0.0)
    daily_carry = (2 * 0.0365 + 0.0365) / 365
    expected = 100 * np.cumprod([1.0, 1 + 0.3 - daily_carry, 1 - 0.3 - daily_carry, 1 - daily_carry])
    assert np.allclose(out["close"].to_numpy(), expected)
    # Intraday high maps to 3x the underlying's move from the previous close.
    assert out["high"].iloc[1] == pytest.approx(100 * (1 + 3 * (111.1 / 100 - 1)))
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()
    assert (out["low"] <= out[["open", "close"]].min(axis=1)).all()


def test_synthetic_fund_uses_total_return_and_floors_at_zero() -> None:
    index = pd.bdate_range("2020-01-06", periods=3)
    under = _frame(np.array([100.0, 60.0, 90.0]), index)
    out = synthetic_leveraged_ohlc(under, 3, pd.Series(0.0, index=index), expense_ratio=0.0, financing_spread=0.0)
    assert out["close"].iloc[1] == pytest.approx(1e-6)  # -40% x 3 wipes the fund out
    assert out["close"].iloc[2] == pytest.approx(1e-6)  # and it never comes back


def test_rate_lookup_uses_last_published_value() -> None:
    rate = pd.Series([0.01, 0.02], index=pd.to_datetime(["2020-01-01", "2020-02-01"]))
    lookup = rate_lookup(rate, -0.001)
    assert lookup(datetime(2019, 12, 31).date()) == 0.0
    assert lookup(datetime(2020, 1, 15).date()) == pytest.approx(0.009)
    assert lookup(datetime(2020, 3, 1).date()) == pytest.approx(0.019)


def _run(close: np.ndarray, sma: int | None) -> tuple[Portfolio, TrendFilteredHoldStrategy]:
    index = pd.bdate_range("2020-01-01", periods=len(close))
    frames = {"IDX": _frame(close, index)}
    frames["IDX_3X"] = synthetic_leveraged_ohlc(frames["IDX"], 3, pd.Series(0.0, index=index))
    data = PublicOHLCVDataHandler(
        DataHandlerConfig(symbols=list(frames), start=index[0].to_pydatetime(), end=index[-1].to_pydatetime(),
                          adjust_prices=False),
        source=FrameSource(frames),
    )
    strategy = TrendFilteredHoldStrategy("IDX", "IDX_3X", sma_window=sma)
    portfolio = Portfolio(initial_cash=100_000.0, leverage=1.0)
    BacktestEngine(data_handler=data, strategy=strategy, portfolio=portfolio,
                   execution_handler=SimulatedExecutionHandler(ExecutionConfig())).run()
    return portfolio, strategy


def test_filter_goes_long_above_average_and_exits_below() -> None:
    up = np.linspace(100, 130, 30)
    down = np.linspace(129, 90, 30)
    portfolio, strategy = _run(np.concatenate([up, down]), sma=10)
    assert strategy.switches == 2
    trade = portfolio.closed_trades[0]
    assert trade.symbol == "IDX_3X"
    # Entry fills on the bar after the 10th close, the first day the average exists.
    assert trade.entry_timestamp == pd.bdate_range("2020-01-01", periods=11)[-1]
    assert portfolio.positions["IDX_3X"].quantity == 0


def test_buy_and_hold_invests_once_and_never_sells() -> None:
    close = np.concatenate([np.linspace(100, 130, 30), np.linspace(129, 90, 30)])
    portfolio, strategy = _run(close, sma=None)
    assert strategy.switches == 1
    assert portfolio.positions["IDX_3X"].quantity > 0
    assert not portfolio.closed_trades
