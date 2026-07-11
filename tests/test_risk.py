"""Every veto path of the Risk Manager. Phase 0 gate: these must all pass."""

from datetime import datetime, timedelta

import pytest

from atis.mktcalendar import IST
from atis.models import Kind, OrderRequest, OrderType, Position, Side
from tests.conftest import T0

_seq = 0


def make_req(symbol="RELIANCE", side=Side.BUY, qty=25, limit=100.0):
    global _seq
    _seq += 1
    return OrderRequest(
        client_order_id=f"ord-{_seq}", signal_id=f"sig-{_seq}", symbol=symbol,
        kind=Kind.EQUITY, side=side, qty=qty, order_type=OrderType.LIMIT,
        limit_price=limit,
    )


def test_valid_entry_approved(risk, make_quote):
    # qty 25 @ ₹100, stop ₹99 → risk ₹25 (limit ₹100), exposure ₹2,500 (limit ₹3,000)
    d = risk.evaluate(make_req(), make_quote(), {}, T0, stop_loss=99.0)
    assert d.approved, d.reasons


def test_oversized_risk_vetoed(risk, make_quote):
    # qty 200 @ ₹100, stop ₹99 → risk ₹200 > 1% of ₹10,000
    d = risk.evaluate(make_req(qty=200), make_quote(), {}, T0, stop_loss=99.0)
    assert not d.approved
    assert any("trade risk" in r for r in d.reasons)
    assert any("concentration" in r for r in d.reasons)  # ₹20,000 exposure too


def test_missing_stop_vetoed(risk, make_quote):
    d = risk.evaluate(make_req(), make_quote(), {}, T0, stop_loss=None)
    assert not d.approved
    assert any("without a stop loss" in r for r in d.reasons)


def test_stop_on_wrong_side_vetoed(risk, make_quote):
    d = risk.evaluate(make_req(), make_quote(), {}, T0, stop_loss=101.0)
    assert not d.approved
    assert any("wrong side" in r for r in d.reasons)


def test_entry_after_cutoff_vetoed(risk, make_quote):
    late = datetime(2026, 7, 10, 15, 5, tzinfo=IST)
    d = risk.evaluate(make_req(), make_quote(t=late), {}, late, stop_loss=99.0)
    assert not d.approved
    assert any("no new entries" in r for r in d.reasons)


def test_exit_after_cutoff_allowed(risk, make_quote):
    late = datetime(2026, 7, 10, 15, 5, tzinfo=IST)
    d = risk.evaluate(
        make_req(side=Side.SELL), make_quote(t=late),
        {"RELIANCE": Position("RELIANCE", Kind.EQUITY, 25, 100.0)},
        late, is_exit=True,
    )
    assert d.approved, d.reasons


def test_market_closed_vetoed(risk, make_quote):
    sunday = datetime(2026, 7, 12, 10, 0, tzinfo=IST)
    d = risk.evaluate(make_req(), make_quote(t=sunday), {}, sunday, stop_loss=99.0)
    assert not d.approved
    assert any("market closed" in r for r in d.reasons)


def test_stale_quote_vetoed(risk, make_quote):
    q = make_quote(asof=T0 - timedelta(seconds=120))
    d = risk.evaluate(make_req(), q, {}, T0, stop_loss=99.0)
    assert not d.approved
    assert any("stale" in r for r in d.reasons)


def test_price_deviation_vetoed(risk, make_quote):
    d = risk.evaluate(make_req(limit=110.0), make_quote(last=100.0), {}, T0,
                      stop_loss=99.0)
    assert not d.approved
    assert any("deviates" in r for r in d.reasons)


def test_duplicate_signal_vetoed(conn, risk, make_quote):
    req = make_req()
    conn.execute(
        "INSERT INTO orders (client_order_id, signal_id, symbol, kind, side, qty, "
        "order_type, product, status, created_ts, updated_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (req.client_order_id, req.signal_id, req.symbol, "EQUITY", "BUY", 25,
         "LIMIT", "MIS", "FILLED", T0.isoformat(), T0.isoformat()),
    )
    d = risk.evaluate(req, make_quote(), {}, T0, stop_loss=99.0)
    assert not d.approved
    assert any("duplicate" in r for r in d.reasons)


def test_kill_switch_vetoes_everything(risk, killswitch, make_quote):
    killswitch.engage("drill")
    d = risk.evaluate(make_req(), make_quote(), {}, T0, stop_loss=99.0)
    assert not d.approved
    assert any("kill switch" in r for r in d.reasons)


def test_breaker_blocks_entries_not_exits(risk, breakers, make_quote):
    breakers.trip(T0.date(), "test trip")
    d = risk.evaluate(make_req(), make_quote(), {}, T0, stop_loss=99.0)
    assert not d.approved
    assert any("circuit breaker" in r for r in d.reasons)
    d_exit = risk.evaluate(
        make_req(side=Side.SELL), make_quote(),
        {"RELIANCE": Position("RELIANCE", Kind.EQUITY, 25, 100.0)}, T0, is_exit=True,
    )
    assert d_exit.approved, d_exit.reasons


def test_max_open_positions_vetoed(risk, make_quote):
    positions = {
        s: Position(s, Kind.EQUITY, 10, 100.0) for s in ("A", "B", "C")
    }
    d = risk.evaluate(make_req(symbol="NEWONE"), make_quote(symbol="NEWONE"),
                      positions, T0, stop_loss=99.0)
    assert not d.approved
    assert any("max open positions" in r for r in d.reasons)


def test_veto_does_not_burn_rate_budget(conn, risk, make_quote):
    risk.evaluate(make_req(qty=100_000), make_quote(), {}, T0, stop_loss=99.0)
    n = conn.execute("SELECT COUNT(*) FROM order_rate").fetchone()[0]
    assert n == 0


def test_vetoes_are_audited(conn, risk, make_quote):
    risk.evaluate(make_req(qty=100_000), make_quote(), {}, T0, stop_loss=99.0)
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE category='VETO'").fetchone()[0]
    assert n == 1
