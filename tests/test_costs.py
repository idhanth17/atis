"""Locks in the worked examples from docs/PAPER_TRADING_ENGINE.md §5 — to the paisa."""

import pytest

from atis.models import Kind, Side


def test_equity_5000_round_trip(cost_engine):
    buy = cost_engine.for_fill(Kind.EQUITY, Side.BUY, 5000.0)
    sell = cost_engine.for_fill(Kind.EQUITY, Side.SELL, 5000.0)

    assert buy.brokerage == pytest.approx(1.50)       # min(0.03% × 5000, ₹20)
    assert buy.stt == 0.0
    assert buy.stamp == pytest.approx(0.15)
    assert sell.stt == pytest.approx(1.25)            # 0.025% on sell value
    assert sell.stamp == 0.0

    total = buy.total + sell.total
    assert total == pytest.approx(5.30, abs=0.01)     # ≈ ₹5–6 per the doc
    assert total / 5000 == pytest.approx(0.00106, abs=0.0001)


def test_option_1000_round_trip(cost_engine):
    buy = cost_engine.for_fill(Kind.OPTION, Side.BUY, 1000.0)
    sell = cost_engine.for_fill(Kind.OPTION, Side.SELL, 1000.0)

    assert buy.brokerage == 20.0                      # flat per executed order
    assert sell.brokerage == 20.0
    assert sell.stt == pytest.approx(1.0)             # 0.1% on sell premium
    assert buy.gst == pytest.approx(0.18 * (20 + 0.35 + 0.001), abs=0.001)

    total = buy.total + sell.total
    assert total == pytest.approx(49.06, abs=0.01)    # the ₹49 lesson
    # ~4.9% of the position: the option-buying hurdle on small capital
    assert total / 1000 == pytest.approx(0.049, abs=0.001)


def test_equity_brokerage_caps_at_20(cost_engine):
    big = cost_engine.for_fill(Kind.EQUITY, Side.BUY, 500_000.0)
    assert big.brokerage == 20.0


def test_round_trip_helper(cost_engine):
    assert cost_engine.round_trip(Kind.OPTION, 1000.0) == pytest.approx(49.06, abs=0.01)
