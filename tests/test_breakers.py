from datetime import timedelta

from tests.conftest import T0


def test_daily_loss_trips(breakers):
    day = T0.date()
    breakers.check_daily_loss(day, equity=9800.0, day_start_equity=10000.0)
    assert breakers.is_tripped(day) is None          # -2% < 3% limit
    breakers.check_daily_loss(day, equity=9700.0, day_start_equity=10000.0)
    assert "daily loss" in breakers.is_tripped(day)


def test_consecutive_losses_trip(breakers):
    day = T0.date()
    breakers.record_trade_result(day, -50)
    breakers.record_trade_result(day, -30)
    assert breakers.is_tripped(day) is None
    breakers.record_trade_result(day, +40)           # winner resets the streak
    breakers.record_trade_result(day, -10)
    breakers.record_trade_result(day, -10)
    breakers.record_trade_result(day, -10)
    assert "consecutive losing" in breakers.is_tripped(day)


def test_error_rate_trips(breakers):
    for i in range(6):
        breakers.record_error(T0 + timedelta(seconds=i))
    assert "errors in" in breakers.is_tripped(T0.date())


def test_first_trip_reason_is_kept(breakers):
    day = T0.date()
    breakers.trip(day, "first")
    breakers.trip(day, "second")
    assert breakers.is_tripped(day) == "first"
