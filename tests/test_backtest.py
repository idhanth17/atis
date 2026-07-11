"""Phase 1 gate tests: the backtester reproduces a hand-computed P&L to the
rupee including all charges, and the replayer physically cannot leak the
future."""

from datetime import date

import pytest

from atis.backtest import calendar_from_data, load_bars, replay_quotes, run_backtest
from atis.db import connect, init_db
from atis.strategy.gap_and_go import GapAndGoDaily
from tests.conftest import CONFIG_DIR


def make_data_conn(bars):
    """bars: [(symbol, trade_date, open, high, low, close, volume)]"""
    conn = connect(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO ohlcv_daily (symbol, trade_date, open, high, low, close, "
        "volume, source, fetched_at) VALUES (?,?,?,?,?,?,?,'test','now')",
        bars,
    )
    conn.commit()
    return conn


@pytest.fixture
def gap_scenario_conn(tmp_path):
    # Day 1: close 100. Day 2: opens 102 (+2% gap), closes 104.
    # Day 3: opens 103.9 (no gap vs 104) — no trade.
    return make_data_conn([
        ("TEST", "2026-07-06", 99.0, 101.0, 98.0, 100.0, 1_000_000),
        ("TEST", "2026-07-07", 102.0, 105.0, 101.0, 104.0, 1_200_000),
        ("TEST", "2026-07-08", 103.9, 104.5, 103.0, 103.5, 900_000),
    ])


def test_hand_computed_pnl_to_the_rupee(gap_scenario_conn, tmp_path):
    """Every number below is computed by hand from the configs:
    capital ₹10,000, risk 1%, concentration 30%, slippage 0.05%,
    spread est 0.05%, leverage 5x, full cost table in config/costs.yaml.
    """
    report = run_backtest(
        gap_scenario_conn, GapAndGoDaily(capital=10_000.0), CONFIG_DIR,
        ["TEST"], date(2026, 7, 6), date(2026, 7, 8),
        kill_path=tmp_path / "KILL",
    )

    # --- hand computation -------------------------------------------------
    # Signal: entry = 102 × 1.002 = 102.204 → order limit ticked to 102.20
    #         stop  = 102.204 × 0.99 = 101.18196
    #         qty   = min(int(100 / 1.02204), int(3000 / 102.204))
    #               = min(97, 29) = 29  (concentration cap binds)
    # Entry fill (no bid/ask in feed):
    #         102 + half_spread(102 × 0.00025 = 0.0255)
    #             + slippage  (102 × 0.0005  = 0.051)  = 102.0765 → ₹102.08
    # Buy leg, value = 29 × 102.08 = ₹2,960.32:
    #         brokerage 0.888096  txn 0.087921  sebi 0.002960
    #         stamp 0.088810      gst 0.18 × 0.978977 = 0.176216
    #         total 1.244003 → ₹1.24
    # Square-off at close 104: 104 − slippage(0.052) = 103.948 → tick ₹103.95
    # Sell leg, value = 29 × 103.95 = ₹3,014.55:
    #         brokerage 0.904365  stt 0.753638  txn 0.089532  sebi 0.003015
    #         gst 0.18 × 0.996912 = 0.179444    total 1.929994 → ₹1.93
    # Realized: (103.95 − 102.08) × 29 = ₹54.23
    # End equity: 10,000 + 54.23 − 1.24 − 1.93 = ₹10,051.06
    # ----------------------------------------------------------------------
    assert report["trades"] == 1
    assert report["fills"] == 2
    assert report["gross_pnl"] == pytest.approx(54.23, abs=0.005)
    assert report["total_costs"] == pytest.approx(1.24 + 1.93, abs=0.005)
    assert report["end_equity"] == pytest.approx(10_051.06, abs=0.01)
    assert report["net_pnl"] == pytest.approx(51.06, abs=0.01)

    # accounting identity: equity = capital + gross − costs, exactly
    assert report["end_equity"] == pytest.approx(
        report["capital"] + report["gross_pnl"] - report["total_costs"], abs=0.005
    )


def test_no_gap_no_trade(tmp_path):
    conn = make_data_conn([
        ("TEST", "2026-07-06", 100.0, 101.0, 99.0, 100.0, 1_000_000),
        ("TEST", "2026-07-07", 100.2, 101.0, 99.5, 100.5, 1_000_000),  # +0.2% < 1%
    ])
    report = run_backtest(conn, GapAndGoDaily(capital=10_000.0), CONFIG_DIR,
                          ["TEST"], date(2026, 7, 6), date(2026, 7, 7),
                          kill_path=tmp_path / "KILL")
    assert report["trades"] == 0
    assert report["end_equity"] == 10_000.0


def test_replayer_cannot_leak_the_future(gap_scenario_conn):
    """Structural anti-leakage: timestamps strictly non-decreasing, and every
    quote's asof equals its emission time — there is no future to peek at."""
    bars = load_bars(gap_scenario_conn, ["TEST"], date(2026, 7, 6), date(2026, 7, 8))
    prev = None
    for quote, now in replay_quotes(bars):
        assert quote.asof == now
        assert quote.received_at == now
        if prev is not None:
            assert now >= prev
        prev = now


def test_calendar_from_data_marks_gaps_as_holidays():
    days = {date(2026, 7, 6), date(2026, 7, 8)}      # Tue 7th missing
    cal = calendar_from_data(days)
    assert cal.is_trading_day(date(2026, 7, 6))
    assert not cal.is_trading_day(date(2026, 7, 7))  # inferred holiday
    assert not cal.is_trading_day(date(2026, 7, 11)) # Saturday


def test_costs_always_on_in_reports(gap_scenario_conn, tmp_path):
    """There is no --fantasy flag wired anywhere; a profitable gross trade
    must show nonzero charges in the report."""
    report = run_backtest(gap_scenario_conn, GapAndGoDaily(capital=10_000.0),
                          CONFIG_DIR, ["TEST"], date(2026, 7, 6), date(2026, 7, 8),
                          kill_path=tmp_path / "KILL")
    assert report["total_costs"] > 0
    assert report["net_pnl"] < report["gross_pnl"]
