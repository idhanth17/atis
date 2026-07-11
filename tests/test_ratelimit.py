"""Rate limits persist in SQLite — a crash-restart cannot reset them."""

from datetime import timedelta

from atis.ratelimit import OrderRateLimiter
from tests.conftest import T0


def test_per_second_limit(rate_limiter):
    assert rate_limiter.try_acquire(T0)[0]
    assert rate_limiter.try_acquire(T0)[0]
    ok, reason = rate_limiter.try_acquire(T0)
    assert not ok and "orders/second" in reason
    # next second: allowed again
    assert rate_limiter.try_acquire(T0 + timedelta(seconds=1.1))[0]


def test_per_day_limit(conn, limits):
    rl = OrderRateLimiter(conn, per_second=1000, per_day=limits.orders_per_day)
    for i in range(limits.orders_per_day):
        assert rl.try_acquire(T0 + timedelta(seconds=2 * i))[0]
    ok, reason = rl.try_acquire(T0 + timedelta(hours=1))
    assert not ok and "orders/day" in reason


def test_counter_survives_restart(conn, limits):
    rl1 = OrderRateLimiter(conn, per_second=1000, per_day=limits.orders_per_day)
    for i in range(limits.orders_per_day):
        rl1.try_acquire(T0 + timedelta(seconds=2 * i))
    # "restart": a brand-new limiter over the same database
    rl2 = OrderRateLimiter(conn, per_second=1000, per_day=limits.orders_per_day)
    ok, reason = rl2.try_acquire(T0 + timedelta(hours=2))
    assert not ok and "orders/day" in reason
