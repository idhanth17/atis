"""Order rate limiter — SEBI compliance + runaway containment (SECURITY.md §3.2).

Hard limits, far under SEBI's 10 orders/second retail threshold. The counter
lives in SQLite so a crash-restart cannot reset it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from atis.mktcalendar import IST


class OrderRateLimiter:
    def __init__(self, conn: sqlite3.Connection, per_second: int, per_day: int):
        self._conn = conn
        self.per_second = per_second
        self.per_day = per_day

    def try_acquire(self, now: datetime) -> tuple[bool, str]:
        """Reserve one order slot. Returns (ok, deny_reason)."""
        epoch = now.timestamp()
        day = now.astimezone(IST).date().isoformat()

        n_sec = self._conn.execute(
            "SELECT COUNT(*) FROM order_rate WHERE ts_epoch > ?", (epoch - 1.0,)
        ).fetchone()[0]
        if n_sec >= self.per_second:
            return False, f"rate limit: {self.per_second} orders/second reached"

        n_day = self._conn.execute(
            "SELECT COUNT(*) FROM order_rate WHERE day = ?", (day,)
        ).fetchone()[0]
        if n_day >= self.per_day:
            return False, f"rate limit: {self.per_day} orders/day reached"

        self._conn.execute(
            "INSERT INTO order_rate (ts_epoch, day) VALUES (?, ?)", (epoch, day)
        )
        self._conn.commit()
        return True, ""
