"""
Simulators for three Market Wizards methods tested in reports/wizards_new/.

- H1: Marsten Parker style short-term mean reversion (Connors RSI(2) trigger), long only.
- H2: volatility risk premium with Tony Cooper's roll-yield and VRP timing rules.
- H3: Ed Thorp style weekly-reversal statistical arbitrage.

Rules and variants are fixed in reports/wizards_new/PREREGISTRATION.md. These are
small daily loops rather than event-engine strategies, because H1 and H3 scan ~800
stocks every day; timing follows the same conservative conventions as the engine
fixes in PR #1 (signal on close t, fill at the open of t+1, no same-close fills).
"""

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252
YAHOO_URL = (
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1=0&period2=1900000000&interval=1d&events=div%7Csplit"
)


# --------------------------------------------------------------------------- data


def fetch_yahoo_close(symbol: str, cache_dir: str | Path = ".cache/yahoo") -> pd.Series:
    """Daily adjusted close (plain close for indices) from Yahoo's chart API, cached as JSON."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{symbol.replace('^', '%5E')}.json"
    if not path.exists():
        request = urllib.request.Request(
            YAHOO_URL.format(symbol=symbol.replace("^", "%5E")),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())
    result = json.loads(path.read_text())["chart"]["result"][0]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    indicators = result["indicators"]
    adj = indicators.get("adjclose", [{}])[0].get("adjclose")
    values = adj if adj is not None else indicators["quote"][0]["close"]
    series = pd.Series(values, index=index, dtype=float, name=symbol)
    return series[~series.index.duplicated(keep="last")].dropna()


@dataclass
class Panel:
    """Wide, adjusted daily prices for the S&P 500 panel plus a point-in-time membership mask."""

    open: pd.DataFrame
    close: pd.DataFrame
    member: pd.DataFrame  # bool, same shape


def load_panel(
    prices_path: str | Path,
    membership_csv: str | Path,
    start: str = "1997-01-01",
    end: str | None = None,
) -> Panel:
    frame = pd.read_parquet(prices_path, columns=["date", "ticker", "open", "close", "adj_close"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    frame = frame[(frame["close"] > 0) & (frame["open"] > 0) & frame["adj_close"].notna()]
    factor = frame["adj_close"] / frame["close"]
    frame = frame.assign(open=frame["open"] * factor, close=frame["adj_close"])
    open_ = frame.pivot(index="date", columns="ticker", values="open").sort_index()
    close = frame.pivot(index="date", columns="ticker", values="close").reindex_like(open_)

    member = pd.DataFrame(False, index=close.index, columns=close.columns)
    spells = pd.read_csv(membership_csv, parse_dates=["effective_from", "effective_to"])
    for row in spells.itertuples():
        if row.symbol not in member.columns:
            continue
        stop = row.effective_to if pd.notna(row.effective_to) else member.index[-1]
        member.loc[(member.index >= row.effective_from) & (member.index <= stop), row.symbol] = True
    return Panel(open=open_, close=close, member=member)


# --------------------------------------------------------------------------- indicators


def _per_column(frame: pd.DataFrame, func) -> pd.DataFrame:
    """Apply func to each column's own non-NaN history, so gaps and listing dates are respected."""
    out = {}
    for col in frame.columns:
        series = frame[col].dropna()
        out[col] = func(series) if len(series) else series
    return pd.DataFrame(out).reindex(index=frame.index, columns=frame.columns)


def wilder_rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    loss = (-delta).clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    rsi[(loss == 0) & (gain > 0)] = 100.0
    rsi[(loss == 0) & (gain == 0)] = 50.0
    return rsi


# --------------------------------------------------------------------------- metrics


def period_stats(equity: pd.Series, start: str, end: str) -> dict[str, float]:
    sliced = equity.loc[start:end].dropna()
    # Start from the last value before the window so the first day's return counts.
    before = equity.loc[: pd.Timestamp(start) - pd.Timedelta(days=1)].dropna()
    if len(before):
        sliced = pd.concat([before.iloc[-1:], sliced])
    returns = sliced.pct_change().dropna()
    years = (sliced.index[-1] - sliced.index[0]).days / 365.25
    cagr = (sliced.iloc[-1] / sliced.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
    sharpe = returns.mean() / returns.std() * math.sqrt(TRADING_DAYS) if returns.std() > 0 else float("nan")
    drawdown = (sliced / sliced.cummax() - 1.0).min()
    return {
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "max_dd": float(drawdown),
        "worst_day": float(returns.min()),
    }


def daily_tbill(irx: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily T-bill accrual from ^IRX (annual %), using the previous day's quote."""
    rate = irx.reindex(index.union(irx.index)).ffill().reindex(index).shift(1).fillna(0.0)
    return rate.clip(lower=0.0) / 100.0 / TRADING_DAYS


# --------------------------------------------------------------------------- H1


@dataclass
class MeanReversionConfig:
    rsi_entry: float = 5.0
    exit_rule: str = "sma5"  # "sma5" (Connors) or "parker" (first up close / 5-day time stop)
    slots: int = 10
    spy_filter: bool = False
    cost_bps: float = 10.0
    time_stop_days: int = 5


def run_mean_reversion(
    panel: Panel,
    cfg: MeanReversionConfig,
    tbill: pd.Series,
    spy_close: pd.Series | None = None,
    initial: float = 100_000.0,
) -> tuple[pd.Series, pd.DataFrame]:
    close = panel.close
    open_ = panel.open.to_numpy()
    close_np = close.to_numpy()
    rsi = _per_column(close, lambda s: wilder_rsi(s, 2)).to_numpy()
    sma200 = _per_column(close, lambda s: s.rolling(200).mean()).to_numpy()
    sma5 = _per_column(close, lambda s: s.rolling(5).mean()).to_numpy()
    member = panel.member.to_numpy()
    last_valid = close.apply(lambda s: s.last_valid_index())
    last_row = close.index.get_indexer(pd.DatetimeIndex(last_valid.values))
    cost = cfg.cost_bps / 10_000.0
    tb = tbill.reindex(close.index).fillna(0.0).to_numpy()
    if cfg.spy_filter:
        assert spy_close is not None
        spy = spy_close.reindex(close.index).ffill()
        market_ok = (spy > spy.rolling(200).mean()).to_numpy()
    else:
        market_ok = np.ones(len(close), dtype=bool)

    cash = initial
    positions: dict[int, dict] = {}  # col -> {"shares", "entry_px", "entry_row", "held"}
    pending_buys: list[int] = []
    pending_sells: set[int] = set()
    last_px = np.full(close.shape[1], np.nan)
    equity = np.empty(len(close))
    trades: list[dict] = []
    prev_equity = initial
    names = close.columns

    for i in range(len(close)):
        cash += max(cash, 0.0) * tb[i]
        # ---- open: sells first, then buys sized off the previous close equity
        for col in sorted(pending_sells):
            px = open_[i, col]
            if np.isnan(px):
                continue  # no bar today; try the next open
            pos = positions.pop(col)
            cash += pos["shares"] * px * (1.0 - cost)
            trades.append({"symbol": names[col], "entry": close.index[pos["entry_row"]],
                           "exit": close.index[i], "ret": px * (1 - cost) / (pos["entry_px"]) - 1.0,
                           "days": pos["held"]})
            pending_sells.discard(col)
        target = prev_equity / cfg.slots
        for col in pending_buys:
            px = open_[i, col]
            if np.isnan(px) or col in positions or len(positions) >= cfg.slots:
                continue
            spend = min(target, cash)
            if spend < 0.5 * target:
                continue
            shares = spend / (px * (1.0 + cost))
            cash -= spend
            positions[col] = {"shares": shares, "entry_px": px * (1.0 + cost), "entry_row": i, "held": 0}
        pending_buys = []

        # ---- close: mark, exits, new signals
        row = close_np[i]
        valid = ~np.isnan(row)
        last_px[valid] = row[valid]
        for col in list(positions):
            pos = positions[col]
            if valid[col]:
                pos["held"] += 1
            if last_row[col] == i and i < len(close) - 1:  # leaves the data: sell at last close
                cash += pos["shares"] * row[col] * (1.0 - cost)
                trades.append({"symbol": names[col], "entry": close.index[pos["entry_row"]],
                               "exit": close.index[i], "ret": row[col] * (1 - cost) / pos["entry_px"] - 1.0,
                               "days": pos["held"], "delisted": True})
                positions.pop(col)
                pending_sells.discard(col)
                continue
            if not valid[col] or col in pending_sells:
                continue
            if cfg.exit_rule == "sma5":
                exit_now = row[col] > sma5[i, col]
            else:
                prev = close_np[i - 1, col] if i > 0 else np.nan
                exit_now = (row[col] > prev) or pos["held"] >= cfg.time_stop_days
            if exit_now:
                pending_sells.add(col)

        value = cash + sum(p["shares"] * last_px[c] for c, p in positions.items())
        equity[i] = value
        prev_equity = value

        free = cfg.slots - len(positions) + len(pending_sells)
        if free > 0 and market_ok[i]:
            signal = valid & member[i] & (row > sma200[i]) & (rsi[i] < cfg.rsi_entry)
            cand = np.flatnonzero(signal)
            cand = [c for c in cand if c not in positions]
            cand.sort(key=lambda c: (rsi[i, c], names[c]))
            pending_buys = cand[:free]

    return pd.Series(equity, index=close.index, name="equity"), pd.DataFrame(trades)


def equal_weight_members(panel: Panel, tbill: pd.Series | None = None) -> pd.Series:
    returns = panel.close.pct_change(fill_method=None)
    held = panel.member.shift(1, fill_value=False) & returns.notna()
    daily = returns.where(held).mean(axis=1).fillna(0.0)
    return (1.0 + daily).cumprod() * 100_000.0


# --------------------------------------------------------------------------- H2


def vrp_signals(vix: pd.Series, vix3m: pd.Series, spx: pd.Series) -> pd.DataFrame:
    """Cooper's two timing signals, computed on day-t closes (True = short volatility)."""
    index = vix.index
    ratio = (vix3m / vix).reindex(index)
    roll = ratio.rolling(10).mean() > 1.0
    hv10 = np.log(spx).diff().rolling(10).std() * math.sqrt(TRADING_DAYS) * 100.0
    spread = (vix - hv10.reindex(index)).rolling(5).mean()
    vrp = spread > 0.0
    out = pd.DataFrame({"roll": roll.astype(object), "vrp": vrp.astype(object)})
    out.loc[ratio.rolling(10).mean().isna(), "roll"] = np.nan
    out.loc[spread.isna(), "vrp"] = np.nan
    return out


def run_vrp(
    signal: pd.Series,
    short_vol: pd.Series,
    long_vol: pd.Series,
    tbill: pd.Series,
    off: str = "long_vol",
    on_weight: float = 1.0,
    cost_bps: float = 10.0,
    initial: float = 100_000.0,
) -> pd.Series:
    """
    signal: True/False on day t's close. The switch trades at the close of t+1, so the
    weights earn the close-to-close return of t+2 onward.
    Weights are reset daily (fixed fractions), costs charged on weight changes.
    """
    index = short_vol.index
    sig = signal.reindex(index).shift(1)  # trade at close of t+1 ...
    r_short = short_vol.pct_change(fill_method=None).fillna(0.0)
    r_long = long_vol.reindex(index).pct_change(fill_method=None).fillna(0.0)
    tb = tbill.reindex(index).fillna(0.0)
    w_short = pd.Series(np.where(sig == True, on_weight, 0.0), index=index)  # noqa: E712
    w_long = pd.Series(np.where((sig == False) & (off == "long_vol"), 1.0, 0.0), index=index)  # noqa: E712
    w_short[sig.isna()] = 0.0
    w_long[sig.isna()] = 0.0
    # ... and earn from the next day on.
    ws, wl = w_short.shift(1).fillna(0.0), w_long.shift(1).fillna(0.0)
    w_cash = 1.0 - ws - wl
    gross = ws * r_short + wl * r_long + w_cash * tb
    turnover = (w_short.diff().abs() + w_long.diff().abs()).fillna(0.0)
    daily = gross - turnover * cost_bps / 10_000.0
    return (1.0 + daily).cumprod() * initial


# --------------------------------------------------------------------------- H3


@dataclass
class StatArbConfig:
    quantile: float = 0.10
    long_only: bool = False
    cost_bps: float = 10.0
    borrow_bps_yr: float = 50.0
    lookback: int = 5
    min_history: int = 60


def run_stat_arb(
    panel: Panel,
    cfg: StatArbConfig,
    spy_close: pd.Series,
    tbill: pd.Series,
    initial: float = 100_000.0,
) -> pd.Series:
    """
    Weekly reversal book. Ranked on the last close of each week, traded at the next open,
    held open-to-open until the next rebalance. Equity is marked at each open.
    """
    close = panel.close
    index = close.index
    # Mark price: today's open if it exists, else the last close (a name that leaves the
    # data is valued flat at its last close, i.e. it is sold there).
    mark = panel.open.where(panel.open.notna(), close.ffill())
    mark = mark.where(close.ffill().notna())
    ret_oo = (mark.shift(-1) / mark - 1.0).fillna(0.0).to_numpy()  # open i -> open i+1
    spy = spy_close.reindex(index).ffill()
    rel = close.pct_change(cfg.lookback, fill_method=None).sub(spy.pct_change(cfg.lookback), axis=0)
    history = close.notna().cumsum()
    eligible = panel.member & (history >= cfg.min_history) & rel.notna() & close.notna()
    week = index.to_period("W-FRI")
    rebalance = np.r_[week[1:] != week[:-1], False]  # last trading day of each week
    tb = tbill.reindex(index).fillna(0.0).to_numpy()
    cost = cfg.cost_bps / 10_000.0
    borrow = cfg.borrow_bps_yr / 10_000.0 / TRADING_DAYS

    weights = np.zeros(close.shape[1])
    equity = np.full(len(index), np.nan)
    value = initial
    rel_np, elig_np = rel.to_numpy(), eligible.to_numpy()
    pending: np.ndarray | None = None
    for i in range(len(index) - 1):
        if pending is not None:  # trade at this open
            value -= value * cost * np.abs(pending - weights).sum()
            weights = pending
            pending = None
        equity[i] = value
        # hold from open i to open i+1
        pnl = float(np.dot(weights, ret_oo[i]))
        short_notional = -weights[weights < 0].sum()
        value *= 1.0 + pnl + tb[i] - borrow * short_notional
        # drift weights
        gross = weights * (1.0 + ret_oo[i])
        weights = gross / (1.0 + pnl) if (1.0 + pnl) > 0 else gross
        if rebalance[i]:
            cols = np.flatnonzero(elig_np[i])
            if len(cols) >= 50:
                order = cols[np.argsort(rel_np[i, cols], kind="stable")]
                n = max(1, int(len(order) * cfg.quantile))
                target = np.zeros_like(weights)
                target[order[:n]] = 1.0 / n
                if not cfg.long_only:
                    target[order[-n:]] = -1.0 / n
                pending = target
    equity[-1] = value
    return pd.Series(equity, index=index, name="equity").ffill()
