"""Technical indicators — pure Python over plain sequences.

Every function returns a list the same length as its input, padded with None
where the indicator is undefined. No pandas dependency in the core: these run
identically in backtest, paper, and live, and are trivially unit-testable
against hand-computed values.
"""

from __future__ import annotations

import math


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    window_sum = sum(values[:n])
    out[n - 1] = window_sum / n
    for i in range(n, len(values)):
        window_sum += values[i] - values[i - n]
        out[i] = window_sum / n
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    """EMA seeded with the SMA of the first n values (standard convention)."""
    out: list[float | None] = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n + 1:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / n, losses / n

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[n] = _rsi(avg_gain, avg_loss)
    for i in range(n + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(delta, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-delta, 0.0)) / n
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal_n: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast, ema_slow = ema(values, fast), ema(values, slow)
    line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    defined = [v for v in line if v is not None]
    sig_defined = ema(defined, signal_n)
    sig: list[float | None] = [None] * len(values)
    j = 0
    for i, v in enumerate(line):
        if v is not None:
            sig[i] = sig_defined[j]
            j += 1
    hist = [
        (l - s) if l is not None and s is not None else None
        for l, s in zip(line, sig)
    ]
    return line, sig, hist


def bollinger(
    values: list[float], n: int = 20, k: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (mid, upper, lower). Population std dev, per convention."""
    mid = sma(values, n)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        m = mid[i]
        assert m is not None
        var = sum((v - m) ** 2 for v in window) / n
        sd = math.sqrt(var)
        upper[i] = m + k * sd
        lower[i] = m - k * sd
    return mid, upper, lower


def atr(
    highs: list[float], lows: list[float], closes: list[float], n: int = 14
) -> list[float | None]:
    """Wilder's ATR from daily bars."""
    length = len(closes)
    out: list[float | None] = [None] * length
    if length < n + 1:
        return out
    trs = []
    for i in range(1, length):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    prev = sum(trs[:n]) / n
    out[n] = prev
    for i in range(n + 1, length):
        prev = (prev * (n - 1) + trs[i - 1]) / n
        out[i] = prev
    return out


def rolling_vwap(
    closes: list[float], volumes: list[float], n: int
) -> list[float | None]:
    """Volume-weighted average price over a rolling n-bar window.
    (True intraday VWAP needs intraday data — Phase 2's quote recorder.)"""
    out: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        pv = sum(closes[j] * volumes[j] for j in range(i - n + 1, i + 1))
        vol = sum(volumes[i - n + 1 : i + 1])
        out[i] = pv / vol if vol > 0 else None
    return out
