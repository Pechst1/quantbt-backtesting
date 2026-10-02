from __future__ import annotations

from collections import deque

import numpy as np

from quantbt.core.events import MarketEvent, SignalEvent
from quantbt.strategy.base import BaseStrategy


def _position_qty(strategy: BaseStrategy, symbol: str) -> float:
    if strategy.portfolio is None:
        return 0.0
    return strategy.portfolio.position_for_symbol(symbol).quantity


def _ema(values: np.ndarray, span: int) -> float:
    if len(values) == 0:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    current = float(values[0])
    for value in values[1:]:
        current = alpha * float(value) + (1.0 - alpha) * current
    return current


def _ema_series(values: np.ndarray, span: int) -> np.ndarray:
    if len(values) == 0:
        return np.array([], dtype=float)
    alpha = 2.0 / (span + 1.0)
    out = np.empty(len(values), dtype=float)
    out[0] = float(values[0])
    for i in range(1, len(values)):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(values: np.ndarray, period: int) -> float:
    if len(values) <= period:
        return 50.0
    deltas = np.diff(values)
    recent = deltas[-period:]
    gains = np.where(recent > 0, recent, 0.0)
    losses = np.where(recent < 0, -recent, 0.0)
    avg_gain = float(gains.mean())
    avg_loss = float(losses.mean())
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = np.empty(len(closes) - 1, dtype=float)
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs[i - 1] = tr
    return float(trs[-period:].mean())


def _cci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
    if len(closes) < period:
        return 0.0
    typical = (highs + lows + closes) / 3.0
    window = typical[-period:]
    sma = float(window.mean())
    mean_dev = float(np.mean(np.abs(window - sma)))
    if mean_dev == 0.0:
        return 0.0
    return (float(typical[-1]) - sma) / (0.015 * mean_dev)


class EmaCrossStrategy(BaseStrategy):
    """Trend following: long when fast EMA > slow EMA, short on bearish crossover."""

    def __init__(self, symbols: list[str], fast_window: int = 12, slow_window: int = 26) -> None:
        super().__init__(symbols=symbols)
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self._prices = {symbol: deque(maxlen=slow_window + 5) for symbol in symbols}
        self._regime = {symbol: 0 for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < self.slow_window:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            fast = _ema(values[-self.fast_window * 3 :], self.fast_window)
            slow = _ema(values, self.slow_window)
            regime = 1 if fast > slow else -1
            prev = self._regime[symbol]
            qty = _position_qty(self, symbol)

            if regime > 0 and prev <= 0 and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif regime < 0 and prev >= 0 and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
            self._regime[symbol] = regime
        return signals


class RsiMeanReversionStrategy(BaseStrategy):
    """Mean reversion: buy oversold RSI, sell overbought RSI."""

    def __init__(
        self,
        symbols: list[str],
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ) -> None:
        super().__init__(symbols=symbols)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self._prices = {symbol: deque(maxlen=rsi_period + 10) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) <= self.rsi_period:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            rsi = _rsi(values, self.rsi_period)
            qty = _position_qty(self, symbol)
            if rsi < self.oversold and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif rsi > self.overbought and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class BollingerMeanReversionStrategy(BaseStrategy):
    """Mean reversion: fade Bollinger Band extremes back toward the moving average."""

    def __init__(
        self,
        symbols: list[str],
        window: int = 20,
        std_multiplier: float = 2.0,
    ) -> None:
        super().__init__(symbols=symbols)
        self.window = window
        self.std_multiplier = std_multiplier
        self._prices = {symbol: deque(maxlen=window + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < self.window:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            window = values[-self.window :]
            mean = float(window.mean())
            std = float(window.std(ddof=0))
            if std <= 0.0:
                continue

            lower = mean - self.std_multiplier * std
            upper = mean + self.std_multiplier * std
            qty = _position_qty(self, symbol)
            if bar.close < lower and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif bar.close > upper and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class DonchianBreakoutStrategy(BaseStrategy):
    """Breakout: enter when price exceeds recent N-day highs/lows."""

    def __init__(self, symbols: list[str], lookback: int = 20) -> None:
        super().__init__(symbols=symbols)
        self.lookback = lookback
        self._highs = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            if len(self._highs[symbol]) <= self.lookback:
                continue

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            prior_high = float(np.max(highs[-self.lookback - 1 : -1]))
            prior_low = float(np.min(lows[-self.lookback - 1 : -1]))
            qty = _position_qty(self, symbol)

            if bar.close > prior_high and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif bar.close < prior_low and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class MacdSignalStrategy(BaseStrategy):
    """Trend following: trade MACD line crossovers against the signal line."""

    def __init__(
        self,
        symbols: list[str],
        fast_window: int = 12,
        slow_window: int = 26,
        signal_window: int = 9,
    ) -> None:
        super().__init__(symbols=symbols)
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.signal_window = signal_window
        maxlen = slow_window + signal_window + 20
        self._prices = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._regime = {symbol: 0 for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < self.slow_window + self.signal_window:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            ema_fast = _ema_series(values, self.fast_window)
            ema_slow = _ema_series(values, self.slow_window)
            macd = ema_fast - ema_slow
            signal = _ema(macd, self.signal_window)
            regime = 1 if float(macd[-1]) > signal else -1
            prev = self._regime[symbol]
            qty = _position_qty(self, symbol)

            if regime > 0 and prev <= 0 and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif regime < 0 and prev >= 0 and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
            self._regime[symbol] = regime
        return signals


class StochasticOscillatorStrategy(BaseStrategy):
    """Momentum reversal: use %K/%D crossover in overbought/oversold zones."""

    def __init__(
        self,
        symbols: list[str],
        lookback: int = 14,
        signal_window: int = 3,
        oversold: float = 20.0,
        overbought: float = 80.0,
    ) -> None:
        super().__init__(symbols=symbols)
        self.lookback = lookback
        self.signal_window = signal_window
        self.oversold = oversold
        self.overbought = overbought
        self._highs = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=lookback + 5) for symbol in symbols}
        self._k_values = {symbol: deque(maxlen=signal_window + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue

            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            if len(self._highs[symbol]) < self.lookback:
                continue

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            highest = float(np.max(highs[-self.lookback :]))
            lowest = float(np.min(lows[-self.lookback :]))
            denom = highest - lowest
            if denom <= 0.0:
                continue
            k_value = 100.0 * ((float(closes[-1]) - lowest) / denom)
            self._k_values[symbol].append(k_value)
            if len(self._k_values[symbol]) <= self.signal_window:
                continue

            k_vals = np.fromiter(self._k_values[symbol], dtype=float)
            d_curr = float(np.mean(k_vals[-self.signal_window :]))
            d_prev = float(np.mean(k_vals[-self.signal_window - 1 : -1]))
            k_prev = float(k_vals[-2])
            k_curr = float(k_vals[-1])
            qty = _position_qty(self, symbol)

            bullish_cross = k_prev <= d_prev and k_curr > d_curr and k_curr < self.oversold
            bearish_cross = k_prev >= d_prev and k_curr < d_curr and k_curr > self.overbought
            if bullish_cross and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif bearish_cross and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class CciMeanReversionStrategy(BaseStrategy):
    """Mean reversion: use Commodity Channel Index extremes around +/-100."""

    def __init__(self, symbols: list[str], period: int = 20, threshold: float = 100.0) -> None:
        super().__init__(symbols=symbols)
        self.period = period
        self.threshold = threshold
        self._highs = {symbol: deque(maxlen=period + 5) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=period + 5) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=period + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            if len(self._closes[symbol]) < self.period:
                continue

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            cci = _cci(highs, lows, closes, self.period)
            qty = _position_qty(self, symbol)
            if cci < -self.threshold and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif cci > self.threshold and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class Momentum121Strategy(BaseStrategy):
    """Cross-sectional style momentum proxy: go with 12-1 month return direction."""

    def __init__(
        self,
        symbols: list[str],
        lookback: int = 252,
        skip: int = 21,
        threshold: float = 0.0,
    ) -> None:
        super().__init__(symbols=symbols)
        self.lookback = lookback
        self.skip = skip
        self.threshold = threshold
        self._prices = {symbol: deque(maxlen=lookback + skip + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        needed = self.lookback + self.skip + 1
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < needed:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            old_price = float(values[-(self.lookback + self.skip)])
            recent_price = float(values[-self.skip])
            if old_price <= 0.0:
                continue
            momentum = recent_price / old_price - 1.0
            qty = _position_qty(self, symbol)
            if momentum > self.threshold and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif momentum < -self.threshold and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class AtrBreakoutStrategy(BaseStrategy):
    """Volatility breakout: trade moves beyond previous close +/- k*ATR."""

    def __init__(
        self,
        symbols: list[str],
        atr_period: int = 14,
        atr_multiplier: float = 1.0,
    ) -> None:
        super().__init__(symbols=symbols)
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        maxlen = atr_period + 20
        self._highs = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=maxlen) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=maxlen) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            if len(self._closes[symbol]) < self.atr_period + 1:
                continue

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            closes = np.fromiter(self._closes[symbol], dtype=float)
            atr = _atr(highs, lows, closes, self.atr_period)
            if atr <= 0.0:
                continue
            upper = float(closes[-2]) + self.atr_multiplier * atr
            lower = float(closes[-2]) - self.atr_multiplier * atr
            qty = _position_qty(self, symbol)

            if float(closes[-1]) > upper and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif float(closes[-1]) < lower and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class TurtleBreakoutStrategy(BaseStrategy):
    """Classic Turtle system: 55-day breakout entries with 20-day exit channel."""

    def __init__(
        self,
        symbols: list[str],
        entry_window: int = 55,
        exit_window: int = 20,
    ) -> None:
        super().__init__(symbols=symbols)
        if exit_window >= entry_window:
            raise ValueError("exit_window should be smaller than entry_window.")
        self.entry_window = entry_window
        self.exit_window = exit_window
        self._highs = {symbol: deque(maxlen=entry_window + 5) for symbol in symbols}
        self._lows = {symbol: deque(maxlen=entry_window + 5) for symbol in symbols}
        self._closes = {symbol: deque(maxlen=entry_window + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._highs[symbol].append(bar.high)
            self._lows[symbol].append(bar.low)
            self._closes[symbol].append(bar.close)
            if len(self._closes[symbol]) <= self.entry_window:
                continue

            highs = np.fromiter(self._highs[symbol], dtype=float)
            lows = np.fromiter(self._lows[symbol], dtype=float)
            close = float(self._closes[symbol][-1])
            qty = _position_qty(self, symbol)

            entry_high = float(np.max(highs[-self.entry_window - 1 : -1]))
            entry_low = float(np.min(lows[-self.entry_window - 1 : -1]))
            exit_high = float(np.max(highs[-self.exit_window - 1 : -1]))
            exit_low = float(np.min(lows[-self.exit_window - 1 : -1]))

            if qty > 0 and close < exit_low:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
            elif qty < 0 and close > exit_high:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif close > entry_high and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif close < entry_low and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals


class ZScoreMeanReversionStrategy(BaseStrategy):
    """Mean reversion: trade standardized distance from rolling mean (z-score)."""

    def __init__(
        self,
        symbols: list[str],
        window: int = 20,
        entry_z: float = 1.5,
    ) -> None:
        super().__init__(symbols=symbols)
        self.window = window
        self.entry_z = entry_z
        self._prices = {symbol: deque(maxlen=window + 5) for symbol in symbols}

    def on_data(self, market_event: MarketEvent) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol in self.symbols:
            bar = market_event.bars.get(symbol)
            if bar is None:
                continue
            self._prices[symbol].append(bar.close)
            if len(self._prices[symbol]) < self.window:
                continue

            values = np.fromiter(self._prices[symbol], dtype=float)
            window = values[-self.window :]
            mean = float(window.mean())
            std = float(window.std(ddof=0))
            if std <= 0.0:
                continue
            z_score = (float(values[-1]) - mean) / std
            qty = _position_qty(self, symbol)
            if z_score < -self.entry_z and qty <= 0:
                signals.append(self.buy(timestamp=market_event.timestamp, symbol=symbol))
            elif z_score > self.entry_z and qty >= 0:
                signals.append(self.sell(timestamp=market_event.timestamp, symbol=symbol))
        return signals

