"""Phase 0 exit gate (README roadmap): a dummy strategy emits a signal that
gets VETOED and AUDITED, and nothing reaches the broker. If this file fails,
the safety rails are broken — do not build further until it's green."""

import uuid
from datetime import datetime, timedelta

from atis.engine import TradingEngine
from atis.mktcalendar import IST
from atis.models import Kind, Quote, Side, Signal
from atis.strategy.base import OversizedDummyStrategy, Strategy
from tests.conftest import T0


def build_engine(conn, strategy, risk, broker, audit, calendar, limits):
    return TradingEngine(conn, strategy, risk, broker, audit, calendar, limits)


def test_oversized_signal_is_vetoed_and_audited(conn, risk, broker, audit,
                                                calendar, limits, make_quote):
    engine = build_engine(conn, OversizedDummyStrategy(), risk, broker, audit,
                          calendar, limits)
    engine.on_quote(make_quote(last=2940.50), T0)

    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    vetoes = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE category='VETO'"
    ).fetchone()[0]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    assert signals == 1, "signal must be persisted"
    assert vetoes == 1, "the oversized order must be vetoed and audited"
    assert orders == 0, "nothing may reach the broker"
    assert broker.get_positions() == {}


class WellSizedStrategy(Strategy):
    """Emits one correctly sized signal — must flow through end to end."""

    name = "well-sized"

    def __init__(self):
        self.done = False

    def on_quote(self, quote, now):
        if self.done:
            return []
        self.done = True
        return [Signal(
            signal_id=f"ok-{uuid.uuid4().hex[:8]}", ts=now, symbol=quote.symbol,
            kind=Kind.EQUITY, action=Side.BUY, confidence=0.7,
            entry_price=quote.last, stop_loss=quote.last - 1.0, qty=20,
        )]


def test_well_sized_signal_reaches_broker(conn, risk, broker, audit,
                                          calendar, limits, make_quote):
    engine = build_engine(conn, WellSizedStrategy(), risk, broker, audit,
                          calendar, limits)
    # ₹100 × 20 = ₹2,000 exposure (< ₹3,000 cap), risk ₹20 (< ₹100 cap)
    engine.on_quote(make_quote(last=100.0, bid=99.95, ask=100.0), T0)

    orders = conn.execute("SELECT status FROM orders").fetchall()
    assert len(orders) == 1
    assert orders[0]["status"] in ("FILLED", "OPEN")


def test_engine_squares_off_at_1515(conn, risk, broker, audit, calendar, limits,
                                    make_quote):
    engine = build_engine(conn, WellSizedStrategy(), risk, broker, audit,
                          calendar, limits)
    engine.on_quote(make_quote(last=100.0, bid=99.95, ask=100.0), T0)
    assert broker.get_positions()

    late = datetime(2026, 7, 10, 15, 16, tzinfo=IST)
    engine.on_quote(make_quote(last=100.5, t=late), late)
    assert broker.get_positions() == {}, "everything must be flat after 15:15"
