"""Quote recorder — builds the private intraday archive, one market day at a
time (docs/PAPER_TRADING_ENGINE.md §2). Intraday history cannot be backfilled
from free sources; every session this runs is data that can't be bought later.

Design:
- provider chain: primary (NSE, near-real-time) with fallback (yfinance,
  delayed); each row records which source it came from and whether delayed
- dedupe by (symbol, asof, source): re-polling an unchanged quote is a no-op
- staleness measured and counted, never hidden
- heartbeat in the meta table every cycle (the dead-man watchdog's future
  food source)
- read-only with respect to trading: deliberately IGNORES the kill switch —
  KILL stops orders, not data collection
- calendar-aware: exits immediately on holidays/weekends; fails closed if the
  holiday file doesn't cover the year
"""

from __future__ import annotations

import sqlite3
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from atis.audit import Audit, Category
from atis.mktcalendar import IST, MARKET_CLOSE, MARKET_OPEN, NSECalendar

STALE_AFTER_SECONDS = 120.0


@dataclass
class SessionSummary:
    day: str = ""
    cycles: int = 0
    rows_inserted: int = 0
    duplicates_skipped: int = 0
    errors: int = 0
    stale_quotes: int = 0
    delayed_quotes: int = 0
    started: str = ""
    ended: str = ""
    skipped_reason: str = ""
    per_symbol: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def archive_intraday(conn: sqlite3.Connection, audit: Audit, symbols: list[str],
                     provider) -> dict:
    """EOD job: pull the day's full 1-minute bars into ohlcv_1min.
    Idempotent (INSERT OR IGNORE); run daily after close — Yahoo only keeps
    ~7 days of 1m history, so skipped days age out permanently."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    summary = {"symbols_ok": 0, "symbols_empty": 0, "rows_inserted": 0}
    for sym in symbols:
        bars = provider.intraday_bars(sym)
        if not bars:
            summary["symbols_empty"] += 1
            continue
        summary["symbols_ok"] += 1
        for b in bars:
            cur = conn.execute(
                "INSERT OR IGNORE INTO ohlcv_1min (symbol, ts, open, high, low, "
                "close, volume, source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sym, b["ts"], b["open"], b["high"], b["low"], b["close"],
                 b["volume"], provider.name, fetched_at),
            )
            summary["rows_inserted"] += cur.rowcount
        conn.commit()
    audit.log(Category.SYSTEM, "intraday_archive", **summary)
    return summary


class QuoteRecorder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        audit: Audit,
        calendar: NSECalendar,
        symbols: list[str],
        primary,
        fallback=None,
        cycle_seconds: float = 60.0,
        clock=None,
        sleep_fn=None,
    ):
        self._conn = conn
        self._audit = audit
        self._calendar = calendar
        self._symbols = symbols
        self._primary = primary
        self._fallback = fallback
        self.cycle_seconds = cycle_seconds
        self._clock = clock or (lambda: datetime.now(IST))
        self._sleep = sleep_fn or time_mod.sleep

    # ------------------------------------------------------------------
    def _store(self, quote, source: str, delayed: bool, summary: SessionSummary) -> None:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO quotes (symbol, asof, received_at, last, bid, ask, "
            "source, is_delayed) VALUES (?,?,?,?,?,?,?,?)",
            (quote.symbol, quote.asof.isoformat(), quote.received_at.isoformat(),
             quote.last, quote.bid, quote.ask, source, int(delayed)),
        )
        self._conn.commit()
        if cur.rowcount:
            summary.rows_inserted += 1
            summary.per_symbol[quote.symbol] = summary.per_symbol.get(quote.symbol, 0) + 1
        else:
            summary.duplicates_skipped += 1
        if (quote.received_at - quote.asof).total_seconds() > STALE_AFTER_SECONDS:
            summary.stale_quotes += 1
        if delayed:
            summary.delayed_quotes += 1

    def _heartbeat(self) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('recorder_heartbeat', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self._conn.commit()

    def cycle(self, summary: SessionSummary) -> None:
        for sym in self._symbols:
            quote = None
            if getattr(self._primary, "available", lambda: True)():
                quote = self._primary.live_quote(sym)
                source, delayed = self._primary.name, self._primary.delayed
            if quote is None and self._fallback is not None:
                quote = self._fallback.live_quote(sym)
                source, delayed = self._fallback.name, self._fallback.delayed
            if quote is None:
                summary.errors += 1
                continue
            self._store(quote, source, delayed, summary)
        self._heartbeat()
        summary.cycles += 1

    # ------------------------------------------------------------------
    def run_session(self, force: bool = False, once: bool = False) -> SessionSummary:
        summary = SessionSummary()
        now = self._clock()
        summary.day = now.date().isoformat()
        summary.started = now.isoformat()

        if not force:
            if not self._calendar.is_trading_day(now.date()):
                summary.skipped_reason = f"{now.date()} is not a trading day"
                self._audit.log(Category.SYSTEM, "recorder_skipped",
                                reason=summary.skipped_reason)
                summary.ended = self._clock().isoformat()
                return summary
            if now.time() >= MARKET_CLOSE:
                summary.skipped_reason = "market already closed"
                self._audit.log(Category.SYSTEM, "recorder_skipped",
                                reason=summary.skipped_reason)
                summary.ended = self._clock().isoformat()
                return summary
            while self._clock().time() < MARKET_OPEN:
                self._sleep(10)

        self._audit.log(Category.SYSTEM, "recorder_start", day=summary.day,
                        symbols=self._symbols, once=once, force=force)
        while True:
            cycle_started = time_mod.monotonic()
            self.cycle(summary)
            if once:
                break
            if not force and self._clock().time() >= MARKET_CLOSE:
                break
            leftover = self.cycle_seconds - (time_mod.monotonic() - cycle_started)
            if leftover > 0:
                self._sleep(leftover)

        summary.ended = self._clock().isoformat()
        self._audit.log(Category.SYSTEM, "recorder_end", **{
            k: v for k, v in summary.as_dict().items() if k != "per_symbol"
        })
        return summary
