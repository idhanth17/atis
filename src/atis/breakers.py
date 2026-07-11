"""Circuit breakers: halt all NEW orders for the day when tripped
(SECURITY.md §3.3). Square-offs of existing positions are still allowed —
a breaker must never trap you in a position.

State persists in SQLite so a crash-restart cannot un-trip a breaker.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from atis.audit import Audit, Category


class CircuitBreakers:
    def __init__(
        self,
        conn: sqlite3.Connection,
        audit: Audit,
        daily_loss_limit_pct: float,
        max_consecutive_losses: int,
        error_rate_limit: int,
        error_rate_window_seconds: float,
    ):
        self._conn = conn
        self._audit = audit
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.error_rate_limit = error_rate_limit
        self.error_rate_window_seconds = error_rate_window_seconds

    # -- state helpers -------------------------------------------------
    def _get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM breaker_state WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def _set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO breaker_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # -- public API ----------------------------------------------------
    def is_tripped(self, day: date) -> str | None:
        """Return the trip reason for this day, or None."""
        return self._get(f"tripped:{day.isoformat()}")

    def trip(self, day: date, reason: str) -> None:
        if self.is_tripped(day):
            return  # already tripped; keep the first reason
        self._set(f"tripped:{day.isoformat()}", reason)
        self._audit.log(Category.BREAKER, "tripped", day=day.isoformat(), reason=reason)

    def check_daily_loss(self, day: date, equity: float, day_start_equity: float) -> None:
        loss_pct = (day_start_equity - equity) / day_start_equity
        if loss_pct >= self.daily_loss_limit_pct:
            self.trip(day, f"daily loss {loss_pct:.2%} >= limit {self.daily_loss_limit_pct:.2%}")

    def record_trade_result(self, day: date, pnl: float) -> None:
        key = f"consec_losses:{day.isoformat()}"
        streak = int(self._get(key) or 0)
        streak = streak + 1 if pnl < 0 else 0
        self._set(key, str(streak))
        if streak >= self.max_consecutive_losses:
            self.trip(day, f"{streak} consecutive losing trades")

    def record_error(self, now: datetime) -> None:
        epoch = now.timestamp()
        self._conn.execute("INSERT INTO error_events (ts_epoch) VALUES (?)", (epoch,))
        self._conn.commit()
        n = self._conn.execute(
            "SELECT COUNT(*) FROM error_events WHERE ts_epoch > ?",
            (epoch - self.error_rate_window_seconds,),
        ).fetchone()[0]
        if n > self.error_rate_limit:
            from atis.mktcalendar import IST
            self.trip(
                now.astimezone(IST).date(),
                f"{n} errors in {self.error_rate_window_seconds:.0f}s window",
            )
