"""Indicators verified against hand-computed values."""

import pytest

from atis.indicators import atr, bollinger, ema, macd, rolling_vwap, rsi, sma


def test_sma_hand_computed():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_ema_seeded_with_sma():
    out = ema([2, 4, 6, 8], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(4.0)                 # SMA seed
    assert out[3] == pytest.approx(8 * 0.5 + 4 * 0.5)   # k = 2/(3+1)


def test_rsi_all_gains_is_100():
    out = rsi(list(range(1, 20)), 14)
    assert out[14] == pytest.approx(100.0)


def test_rsi_hand_computed_mixed():
    # 8 up-moves of 1 and 6 down-moves of 1 over 14 periods:
    # avg_gain = 8/14, avg_loss = 6/14 → RS = 8/6 → RSI = 100 − 100/(1+8/6)
    values = [10.0]
    for i in range(14):
        values.append(values[-1] + (1.0 if i < 8 else -1.0))
    out = rsi(values, 14)
    assert out[14] == pytest.approx(100 - 100 / (1 + 8 / 6), abs=1e-9)


def test_macd_shapes_and_warmup():
    values = [float(i) for i in range(1, 61)]
    line, sig, hist = macd(values)
    assert len(line) == len(sig) == len(hist) == 60
    assert line[24] is None and line[25] is not None     # slow EMA warmup
    assert sig[32] is None and sig[33] is not None       # +9 for signal
    assert hist[33] == pytest.approx(line[33] - sig[33])


def test_bollinger_constant_series_collapses():
    mid, upper, lower = bollinger([50.0] * 25, 20, 2.0)
    assert mid[19] == upper[19] == lower[19] == pytest.approx(50.0)


def test_bollinger_hand_computed():
    # window [1..20]: mean 10.5, population std = sqrt(33.25)
    values = [float(i) for i in range(1, 21)]
    mid, upper, lower = bollinger(values, 20, 2.0)
    assert mid[19] == pytest.approx(10.5)
    assert upper[19] == pytest.approx(10.5 + 2 * 33.25 ** 0.5)
    assert lower[19] == pytest.approx(10.5 - 2 * 33.25 ** 0.5)


def test_atr_constant_range():
    n = 14
    highs = [110.0] * 20
    lows = [100.0] * 20
    closes = [105.0] * 20
    out = atr(highs, lows, closes, n)
    assert out[n] == pytest.approx(10.0)     # TR is 10 every bar
    assert out[-1] == pytest.approx(10.0)


def test_rolling_vwap_weights_by_volume():
    closes = [10.0, 20.0]
    volumes = [1.0, 3.0]
    out = rolling_vwap(closes, volumes, 2)
    assert out[1] == pytest.approx((10 * 1 + 20 * 3) / 4)
