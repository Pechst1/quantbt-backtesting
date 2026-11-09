"""Diversified 12-month time-series momentum, as published by Moskowitz, Ooi and Pedersen.

Source: Moskowitz, Ooi, Pedersen, "Time Series Momentum", Journal of Financial
Economics 104 (2012). Parameters are the paper's; nothing is fitted:

- Signal: sign of the instrument's excess return over the past 12 months.
- Size: 40% / ex-ante annualised volatility, where volatility is the exponentially
  weighted standard deviation of daily excess returns with a 60-day center of
  mass, annualised with 261 days.
- Portfolio: equal-weighted average over the instruments available that month
  (weight = sign * 0.40 / sigma / S_t), rebalanced monthly.

Implementation choices (not tuning): signals use the month-end close and trade
at the next day's open; an instrument joins once it has 12 months of history and
60 days of returns; cash and shorts earn, and borrowing pays, the bill-ETF return.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantbt.systems.data import PricePanel, daily_cash_rate
from quantbt.systems.overlay import NativeVolEstimator, VolTarget, capped_scale, financing_rate

# 25 liquid US-listed ETFs covering the four asset classes in the paper, each with
# inception before 2008: equity indices, government and credit bonds, commodities
# and currencies. Pre-declared from asset-class coverage, not from performance.
TSMOM_UNIVERSE: dict[str, str] = {
    "SPY": "equity",
    "QQQ": "equity",
    "IWM": "equity",
    "EFA": "equity",
    "VGK": "equity",
    "EWJ": "equity",
    "EEM": "equity",
    "VNQ": "equity",
    "TLT": "bond",
    "IEF": "bond",
    "TIP": "bond",
    "LQD": "bond",
    "HYG": "bond",
    "GLD": "commodity",
    "SLV": "commodity",
    "USO": "commodity",
    "UNG": "commodity",
    "DBA": "commodity",
    "DBB": "commodity",
    "DBC": "commodity",
    "FXE": "fx",
    "FXY": "fx",
    "FXB": "fx",
    "FXA": "fx",
    "FXC": "fx",
}


@dataclass(frozen=True, slots=True)
class TsmomConfig:
    lookback_months: int = 12
    target_vol: float = 0.40
    vol_com_days: float = 60.0
    vol_min_days: int = 60
    annualization: int = 261
    initial_equity: float = 1_000_000.0
    cost_bps_per_side: float = 9.5
    cash_symbol: str | None = "BIL"
    borrow_spread_bps: float = 0.0


@dataclass(slots=True)
class TsmomResult:
    equity: pd.Series
    gross_leverage: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    config: TsmomConfig
    scale: pd.Series | None = None


def target_weights(panel: PricePanel, symbols: list[str], config: TsmomConfig) -> pd.DataFrame:
    """Weights decided at each month-end close; NaN rows elsewhere."""
    rf = daily_cash_rate(panel, config.cash_symbol)
    close = panel.close[symbols]
    daily = close.pct_change(fill_method=None)
    excess = daily.sub(rf, axis=0)
    sigma = excess.ewm(com=config.vol_com_days, min_periods=config.vol_min_days, ignore_na=True).std() * np.sqrt(
        config.annualization
    )

    # Cumulative excess-return index, so the 12-month signal is in excess of cash.
    growth = (1.0 + excess.fillna(0.0)).cumprod().where(close.notna())
    month_end = close.groupby(close.index.to_period("M")).tail(1).index
    growth_m = growth.loc[month_end].ffill()
    signal = np.sign(growth_m / growth_m.shift(config.lookback_months) - 1.0)

    raw = signal * config.target_vol / sigma.loc[month_end]
    count = raw.notna().sum(axis=1).replace(0, np.nan)
    return raw.div(count, axis=0).fillna(0.0)


def run_tsmom(
    panel: PricePanel,
    config: TsmomConfig | None = None,
    symbols: list[str] | None = None,
    vol_target: VolTarget | None = None,
) -> TsmomResult:
    """Simulate the strategy; with `vol_target`, hold `k` times the published book (see `overlay`)."""
    config = config or TsmomConfig()
    symbols = symbols or [s for s in panel.symbols if s in TSMOM_UNIVERSE]
    if not symbols:
        raise ValueError("No TSMOM universe symbols in panel.")
    cost = config.cost_bps_per_side / 10_000.0
    rf = daily_cash_rate(panel, config.cash_symbol)
    weights = target_weights(panel, symbols, config)

    opens = panel.open[symbols].to_numpy()
    closes = panel.close[symbols].to_numpy()
    index = panel.index
    decision_pos = {index.get_loc(ts): weights.loc[ts].to_numpy() for ts in weights.index}

    native = np.zeros(len(symbols))  # the published book, in shares
    qty = np.zeros(len(symbols))  # shares actually held
    last_close = np.full(len(symbols), np.nan)
    cash = config.initial_equity
    equity = cash
    pending: np.ndarray | None = None
    estimator = NativeVolEstimator(vol_target) if vol_target is not None else None
    scale = 0.0
    native_cash = 0.0
    native_value = 0.0
    equity_hist, lev_hist, turnover_hist, scale_hist = [], [], [], []

    for t in range(len(index)):
        cash *= 1.0 + financing_rate(cash, float(rf.iloc[t]), config.borrow_spread_bps)
        prior_equity = equity
        traded = 0.0
        if pending is not None:
            # Rebalance at today's open to the weights decided at the prior month-end close.
            for j in range(len(symbols)):
                o = opens[t, j]
                if np.isnan(o):
                    continue
                target_qty = pending[j] * prior_equity / o
                delta = target_qty - native[j]
                native_cash -= delta * o + abs(delta * o) * cost
                native[j] = target_qty
            pending = None
        if estimator is None:
            target = native
        else:
            marks = np.where(np.isnan(opens[t]), last_close, opens[t])
            native_gross = float(np.nansum(np.abs(native * marks)))
            k = capped_scale(scale, native_gross, prior_equity, vol_target.max_gross)
            target = k * native
        for j in range(len(symbols)):
            o = opens[t, j]
            if np.isnan(o):
                continue
            delta = target[j] - qty[j]
            if delta != 0.0:
                cash -= delta * o + abs(delta * o) * cost
                traded += abs(delta * o)
                qty[j] = target[j]
        row = closes[t]
        valid = ~np.isnan(row)
        last_close[valid] = row[valid]
        marks = np.nan_to_num(last_close)
        held = np.where(np.isnan(last_close), 0.0, qty * marks)
        equity = cash + held.sum()
        if estimator is not None:
            value = native_cash + float(np.sum(native * marks))
            if prior_equity > 0:
                estimator.update((value - native_value) / prior_equity)
            native_value = value
            scale = estimator.scale()
        equity_hist.append(equity)
        lev_hist.append(np.abs(held).sum() / equity if equity > 0 else np.nan)
        turnover_hist.append(traded / equity if equity > 0 else np.nan)
        scale_hist.append(scale)
        if t in decision_pos and t + 1 < len(index):
            pending = decision_pos[t]
        if equity <= 0:
            break

    out_index = index[: len(equity_hist)]
    return TsmomResult(
        equity=pd.Series(equity_hist, index=out_index, name="tsmom_12m"),
        gross_leverage=pd.Series(lev_hist, index=out_index),
        weights=weights,
        turnover=pd.Series(turnover_hist, index=out_index),
        config=config,
        scale=pd.Series(scale_hist, index=out_index),
    )
