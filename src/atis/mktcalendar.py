"""NSE trading calendar and session clock (IST).

Fails closed: if the holiday file doesn't cover a date's year, trading is
refused rather than assumed open.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class CalendarStaleError(RuntimeError):
    """Holiday list doesn't cover this year — update config/nse_holidays.yaml."""


class NSECalendar:
    def __init__(self, holidays: dict[int, set[date]]):
        self._holidays = holidays

    def _to_ist(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            raise ValueError("naive datetime passed to NSECalendar — always use tz-aware IST/UTC")
        return dt.astimezone(IST)

    def is_trading_day(self, d: date) -> bool:
        if d.year not in self._holidays:
            raise CalendarStaleError(
                f"No NSE holiday data for {d.year}; refusing to assume market is open"
            )
        return d.weekday() < 5 and d not in self._holidays[d.year]

    def is_market_open(self, dt: datetime) -> bool:
        ist = self._to_ist(dt)
        return (
            self.is_trading_day(ist.date())
            and MARKET_OPEN <= ist.time() <= MARKET_CLOSE
        )

    def allows_new_entries(self, dt: datetime, cutoff: time) -> bool:
        ist = self._to_ist(dt)
        return self.is_market_open(dt) and ist.time() < cutoff

    def square_off_due(self, dt: datetime, at: time) -> bool:
        ist = self._to_ist(dt)
        return self.is_trading_day(ist.date()) and ist.time() >= at
