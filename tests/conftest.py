"""Shared fixtures. Tests load the real config/ YAML files so the shipped
config is validated on every test run."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from atis import config as cfg
from atis.audit import Audit
from atis.breakers import CircuitBreakers
from atis.broker.paper import PaperBroker
from atis.costs import CostEngine
from atis.db import connect, init_db
from atis.killswitch import KillSwitch
from atis.mktcalendar import IST, NSECalendar
from atis.models import Kind, Quote
from atis.ratelimit import OrderRateLimiter
from atis.risk import RiskManager

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Friday 2026-07-10 is a regular NSE trading day
T0 = datetime(2026, 7, 10, 10, 0, 0, tzinfo=IST)


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def audit(conn):
    return Audit(conn)


@pytest.fixture
def limits():
    return cfg.load_risk(CONFIG_DIR)


@pytest.fixture
def cost_engine():
    return CostEngine(cfg.load_costs(CONFIG_DIR))


@pytest.fixture
def paper_cfg():
    return cfg.load_paper(CONFIG_DIR)


@pytest.fixture
def calendar():
    return NSECalendar(cfg.load_holidays(CONFIG_DIR))


@pytest.fixture
def killswitch(tmp_path):
    return KillSwitch(tmp_path / "KILL")


@pytest.fixture
def breakers(conn, audit, limits):
    return CircuitBreakers(
        conn, audit, limits.daily_loss_limit_pct, limits.max_consecutive_losses,
        limits.error_rate_limit, limits.error_rate_window_seconds,
    )


@pytest.fixture
def rate_limiter(conn, limits):
    return OrderRateLimiter(conn, limits.orders_per_second, limits.orders_per_day)


@pytest.fixture
def risk(conn, limits, calendar, killswitch, breakers, rate_limiter, audit):
    return RiskManager(conn, limits, calendar, killswitch, breakers, rate_limiter, audit)


@pytest.fixture
def broker(conn, audit, cost_engine, paper_cfg, limits):
    return PaperBroker(conn, audit, cost_engine, paper_cfg, limits.capital)


@pytest.fixture
def make_quote():
    def _make(symbol="RELIANCE", last=100.0, bid=None, ask=None, t=T0,
              kind=Kind.EQUITY, asof=None) -> Quote:
        return Quote(symbol=symbol, last=last, bid=bid, ask=ask,
                     asof=asof or t, received_at=t, kind=kind)
    return _make
