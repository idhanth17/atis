"""Append-only audit log. Every signal, veto, order, fill, breaker trip, and
error goes through here. If you can't reconstruct why a trade happened, you
can't debug the system or answer a broker/exchange query.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from atis import CONFIG_VERSION


class Category:
    SYSTEM = "SYSTEM"
    CONFIG = "CONFIG"
    SIGNAL = "SIGNAL"
    VETO = "VETO"
    ORDER = "ORDER"
    FILL = "FILL"
    BREAKER = "BREAKER"
    KILL = "KILL"
    ERROR = "ERROR"
    RECONCILE = "RECONCILE"


class Audit:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def log(self, category: str, event: str, **details) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (ts_utc, category, event, details, config_version) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                category,
                event,
                json.dumps(details, default=str, ensure_ascii=False),
                CONFIG_VERSION,
            ),
        )
        self._conn.commit()

    def tail(self, n: int = 20) -> list[sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed(rows))
