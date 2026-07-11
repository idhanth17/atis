from datetime import date, datetime, time

import pytest

from atis.mktcalendar import IST, CalendarStaleError


def test_regular_trading_day(calendar):
    assert calendar.is_trading_day(date(2026, 7, 10))       # Friday


def test_weekend_closed(calendar):
    assert not calendar.is_trading_day(date(2026, 7, 11))   # Saturday
    assert not calendar.is_trading_day(date(2026, 7, 12))   # Sunday


def test_holiday_closed(calendar):
    assert not calendar.is_trading_day(date(2026, 1, 26))   # Republic Day (Monday)


def test_unknown_year_fails_closed(calendar):
    with pytest.raises(CalendarStaleError):
        calendar.is_trading_day(date(2027, 1, 4))


def test_market_hours(calendar):
    d = (2026, 7, 10)
    assert calendar.is_market_open(datetime(*d, 10, 0, tzinfo=IST))
    assert calendar.is_market_open(datetime(*d, 9, 15, tzinfo=IST))
    assert not calendar.is_market_open(datetime(*d, 9, 14, tzinfo=IST))
    assert not calendar.is_market_open(datetime(*d, 15, 31, tzinfo=IST))


def test_entry_cutoff_and_squareoff(calendar):
    d = (2026, 7, 10)
    cutoff, sq = time(15, 0), time(15, 15)
    assert calendar.allows_new_entries(datetime(*d, 14, 59, tzinfo=IST), cutoff)
    assert not calendar.allows_new_entries(datetime(*d, 15, 0, tzinfo=IST), cutoff)
    assert not calendar.square_off_due(datetime(*d, 15, 14, tzinfo=IST), sq)
    assert calendar.square_off_due(datetime(*d, 15, 15, tzinfo=IST), sq)


def test_naive_datetime_rejected(calendar):
    with pytest.raises(ValueError, match="tz-aware"):
        calendar.is_market_open(datetime(2026, 7, 10, 10, 0))
