from __future__ import annotations

import numpy as np
import pandas as pd


def simulate_target_weights(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    targets: pd.DataFrame,
    cash_rate: pd.Series,
    cost_bps: float = 10.0,
    skid: float | None = None,
    borrow_spread: float = 0.01,
    short_fee: float = 0.005,
    initial_equity: float = 1.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Daily simulation of a portfolio that rebalances to target weights.

    ``targets`` holds one row per signal date (a date of the price index); the weights are
    computed from data up to that date's close and are traded at the NEXT bar's open.
    Symbols missing from a row are set to zero weight; dates without a row do not trade,
    so positions drift with prices. Weights are fractions of equity at the trade open.

    Costs: ``cost_bps`` per side on traded value, or with ``skid`` set, Seykota's fill model
    (buys at open + skid * (high - open), sells at open - skid * (open - low)).
    Cash earns ``cash_rate`` (annual, per date); a debit balance pays cash_rate + borrow_spread;
    short positions pay ``short_fee`` per year on their value. A position whose prices end
    is sold at its last close, with costs.

    Returns (equity, gross exposure / equity, daily traded value / equity).
    """
    index = close.index
    cols = list(close.columns)
    targets = targets.reindex(columns=cols).fillna(0.0)
    O = open_.reindex(index=index, columns=cols).to_numpy(float)
    H = high.reindex(index=index, columns=cols).to_numpy(float)
    L = low.reindex(index=index, columns=cols).to_numpy(float)
    C = close.to_numpy(float)
    rate = cash_rate.reindex(index).ffill().bfill().to_numpy(float)
    signal_rows = {index.get_loc(ts): row.to_numpy(float) for ts, row in targets.iterrows()}

    finite = np.isfinite(C)
    last_valid = np.where(finite.any(axis=0), len(index) - 1 - np.argmax(finite[::-1], axis=0), -1)
    n = len(cols)
    pos = np.zeros(n)
    last_close = np.full(n, np.nan)
    cash = float(initial_equity)
    equity = np.empty(len(index))
    gross = np.empty(len(index))
    turnover = np.zeros(len(index))
    bps = cost_bps / 10_000.0

    for i in range(len(index)):
        if i > 0:
            dt = (index[i] - index[i - 1]).days / 365.0
            r = rate[i - 1]
            cash *= 1.0 + (r if cash >= 0 else r + borrow_spread) * dt
            cash -= short_fee * np.abs(pos[pos < 0]).sum() * dt

        # Positions whose data ended yesterday are sold at their last close.
        dead = (last_valid == i - 1) & (pos != 0)
        if dead.any():
            traded = np.abs(pos[dead]).sum()
            cash += pos[dead].sum() - traded * bps
            turnover[i] += traded
            pos[dead] = 0.0

        o = O[i]
        has_open = np.isfinite(o) & np.isfinite(last_close)
        pos[has_open] *= o[has_open] / last_close[has_open]

        w = signal_rows.get(i - 1)
        if w is not None:
            eq_open = cash + pos.sum()
            want = w * eq_open
            tradable = np.isfinite(o)
            trade = np.where(tradable, want - pos, 0.0)
            if skid is None:
                cost = np.abs(trade).sum() * bps
            else:
                with np.errstate(invalid="ignore", divide="ignore"):
                    up = np.nan_to_num((H[i] - o) / o)
                    down = np.nan_to_num((o - L[i]) / o)
                cost = (np.clip(trade, 0, None) * skid * up).sum() + (np.clip(-trade, 0, None) * skid * down).sum()
            cash -= trade.sum() + cost
            pos += trade
            turnover[i] += np.abs(trade).sum() / eq_open if eq_open else 0.0

        c = C[i]
        has_close = np.isfinite(c)
        base = np.where(np.isfinite(o), o, last_close)
        ok = has_close & np.isfinite(base)
        pos[ok] *= c[ok] / base[ok]
        last_close = np.where(has_close, c, last_close)

        equity[i] = cash + pos.sum()
        gross[i] = np.abs(pos).sum() / equity[i] if equity[i] else np.nan

    return (
        pd.Series(equity, index=index, name="equity"),
        pd.Series(gross, index=index, name="gross"),
        pd.Series(turnover, index=index, name="turnover"),
    )


def period_stats(equity: pd.Series, cash_rate: pd.Series | None = None) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    rets = equity.pct_change().dropna()
    dd = equity / equity.cummax() - 1.0
    out = {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "cagr": float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1),
        "ann_vol": float(rets.std() * np.sqrt(252)),
        "sharpe_rf0": float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else float("nan"),
        "max_drawdown": float(dd.min()),
    }
    if cash_rate is not None:
        days = equity.index.to_series().diff().dt.days.iloc[1:] / 365.0
        rf = cash_rate.reindex(equity.index).ffill().shift(1).iloc[1:] * days
        ex = rets - rf.to_numpy()
        out["sharpe_excess"] = float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else float("nan")
    return out
