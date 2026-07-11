"""PaperBroker acceptance tests (docs/PAPER_TRADING_ENGINE.md §7):
the simulator must be pessimistic, honest about stale data, and its ledger
must balance after every event."""

from datetime import timedelta

import pytest

from atis.broker.paper import r2
from atis.models import Kind, OrderRequest, OrderStatus, OrderType, Side
from tests.conftest import T0

_seq = 0


def make_req(symbol="RELIANCE", side=Side.BUY, qty=10, order_type=OrderType.LIMIT,
             limit=None, trigger=None, kind=Kind.EQUITY):
    global _seq
    _seq += 1
    return OrderRequest(
        client_order_id=f"pord-{_seq}", signal_id=f"psig-{_seq}", symbol=symbol,
        kind=kind, side=side, qty=qty, order_type=order_type,
        limit_price=limit, trigger_price=trigger,
    )


def test_limit_at_touch_does_not_fill(broker, make_quote):
    """The classic paper-trading lie: a touch is not a fill."""
    broker.on_quote(make_quote(last=101.0), T0)
    res = broker.place_order(make_req(limit=100.0), T0)
    assert res.status is OrderStatus.OPEN

    t1 = T0 + timedelta(seconds=30)
    broker.on_quote(make_quote(last=100.0, t=t1), t1)          # touch
    assert broker.get_positions() == {}

    t2 = T0 + timedelta(seconds=60)
    broker.on_quote(make_quote(last=99.95, t=t2), t2)          # traded through
    pos = broker.get_positions()["RELIANCE"]
    assert pos.qty == 10
    assert pos.avg_price == pytest.approx(100.0)


def test_marketable_limit_fills_with_slippage(broker, make_quote):
    broker.on_quote(make_quote(last=100.0, bid=99.95, ask=100.0), T0)
    res = broker.place_order(make_req(limit=100.10), T0)
    assert res.status is OrderStatus.FILLED
    # ask + slippage (0.05% of last = 0.05), capped at limit
    assert res.fills[0].price == pytest.approx(100.05)


def test_market_order_pays_spread_and_double_slippage(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    res = broker.place_order(make_req(order_type=OrderType.MARKET), T0)
    assert res.status is OrderStatus.FILLED
    # last + half_spread(0.025) + 2×slip(0.10) = 100.125 → ₹100.13
    assert res.fills[0].price == pytest.approx(100.13)


def test_gap_through_stop_fills_at_gap_not_trigger(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    broker.place_order(make_req(order_type=OrderType.MARKET), T0)   # long 10

    broker.place_order(
        make_req(side=Side.SELL, order_type=OrderType.SL_M, trigger=98.0), T0
    )
    # price gaps straight through the trigger to 95
    t1 = T0 + timedelta(minutes=5)
    broker.on_quote(make_quote(last=95.0, t=t1), t1)
    assert broker.get_positions() == {}
    fill = broker._conn.execute(
        "SELECT price FROM fills WHERE side='SELL' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert fill["price"] < 98.0                      # NOT the trigger price
    assert fill["price"] == pytest.approx(94.95)     # 95 − slippage, to the paisa


def test_stale_quote_refuses_order(broker, make_quote):
    stale = make_quote(asof=T0 - timedelta(seconds=120))
    broker.on_quote(stale, T0)
    res = broker.place_order(make_req(limit=100.0), T0)
    assert res.status is OrderStatus.REJECTED
    assert "stale" in res.reason.lower() or "unavailable" in res.reason.lower()


def test_margin_rejection_kite_style(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    # ₹100,000 exposure needs ₹20,000 margin at 5x; only ₹10,000 cash
    res = broker.place_order(make_req(qty=1000, limit=100.0), T0)
    assert res.status is OrderStatus.REJECTED
    assert "Insufficient funds" in res.reason


def test_option_buy_debits_full_premium(broker, make_quote):
    q = make_quote(symbol="NIFTY26JUL25000CE", last=100.0, kind=Kind.OPTION)
    broker.on_quote(q, T0)
    cash_before = broker.cash
    res = broker.place_order(
        make_req(symbol="NIFTY26JUL25000CE", qty=65, order_type=OrderType.MARKET,
                 kind=Kind.OPTION), T0
    )
    assert res.status is OrderStatus.FILLED
    premium = 65 * res.fills[0].price
    assert broker.cash == pytest.approx(cash_before - premium - res.fills[0].costs_total,
                                        abs=0.01)


def test_tick_size_enforced(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    res = broker.place_order(make_req(limit=100.07), T0)
    assert res.status is OrderStatus.REJECTED
    assert "tick size" in res.reason


def test_option_writing_rejected(broker, make_quote):
    q = make_quote(symbol="NIFTY26JUL25000PE", last=100.0, kind=Kind.OPTION)
    broker.on_quote(q, T0)
    res = broker.place_order(
        make_req(symbol="NIFTY26JUL25000PE", side=Side.SELL, qty=65,
                 order_type=OrderType.MARKET, kind=Kind.OPTION), T0
    )
    assert res.status is OrderStatus.REJECTED
    assert "not supported" in res.reason


def test_full_round_trip_ledger_balances(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    buy = broker.place_order(make_req(order_type=OrderType.MARKET, qty=20), T0)
    assert buy.status is OrderStatus.FILLED

    t1 = T0 + timedelta(minutes=30)
    broker.on_quote(make_quote(last=102.0, t=t1), t1)
    sell = broker.place_order(
        make_req(side=Side.SELL, order_type=OrderType.MARKET, qty=20), t1
    )
    assert sell.status is OrderStatus.FILLED
    assert broker.get_positions() == {}
    broker._check_invariant()                        # never raises

    m = broker.get_margins()
    assert m["blocked_margin"] == 0.0
    assert m["equity"] == pytest.approx(broker.cash)
    # won on price, but costs were charged on both legs
    gross = (sell.fills[0].price - buy.fills[0].price) * 20
    net = m["equity"] - 10000.0
    assert net == pytest.approx(gross - buy.fills[0].costs_total
                                - sell.fills[0].costs_total, abs=0.02)


def test_square_off_all_closes_everything(broker, make_quote):
    broker.on_quote(make_quote(last=100.0), T0)
    broker.place_order(make_req(order_type=OrderType.MARKET, qty=10), T0)
    assert broker.get_positions()

    t1 = T0 + timedelta(hours=5)
    broker.on_quote(make_quote(last=101.0, t=t1), t1)
    results = broker.square_off_all(t1, penalty=True)
    assert broker.get_positions() == {}
    assert all(r.status in (OrderStatus.FILLED, OrderStatus.CANCELLED) for r in results)
    broker._check_invariant()


def test_state_survives_restart(conn, audit, cost_engine, paper_cfg, limits, make_quote):
    from atis.broker.paper import PaperBroker

    b1 = PaperBroker(conn, audit, cost_engine, paper_cfg, limits.capital)
    b1.on_quote(make_quote(last=100.0), T0)
    b1.place_order(make_req(order_type=OrderType.MARKET, qty=10), T0)
    cash, positions = b1.cash, b1.get_positions()

    # "restart": new broker instance over the same database — no re-seed, no loss
    b2 = PaperBroker(conn, audit, cost_engine, paper_cfg, limits.capital)
    assert b2.cash == pytest.approx(cash)
    assert b2.get_positions().keys() == positions.keys()
    assert b2.get_positions()["RELIANCE"].qty == 10


def test_r2_rounds_half_up():
    assert r2(0.125) == 0.13     # not banker's rounding
    assert r2(0.115) == 0.12
