"""Live-ish quotes from NSE's public quote API.

This is an unofficial, fragile API (README §Free data sources): throttle
politely, keep one honest browser User-Agent, cache the session cookies,
expect breakage, and circuit-break on repeated failure so the caller can
fall back. Quotes carry the exchange's own lastUpdateTime as `asof` — the
staleness honesty everything downstream depends on.

KNOWN LIMITATION: NSE fronts this API with Akamai bot protection and often
403s non-browser clients. We deliberately do NOT evade bot detection
(no TLS-fingerprint impersonation) — when NSE says no, the circuit breaker
opens and the recorder falls back to the delayed provider. Real-time data
arrives properly in Phase 4 via the broker's websocket, the sanctioned
channel.

Requires the 'data' extra (requests).
"""

from __future__ import annotations

import time
from datetime import datetime

from atis.mktcalendar import IST
from atis.models import Kind, Quote

BASE = "https://www.nseindia.com"
QUOTE_URL = f"{BASE}/api/quote-equity"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/",
}
TS_FORMAT = "%d-%b-%Y %H:%M:%S"


def parse_quote_payload(payload: dict, received_at: datetime) -> Quote | None:
    """Pure parser, unit-testable offline."""
    try:
        last = float(payload["priceInfo"]["lastPrice"])
    except (KeyError, TypeError, ValueError):
        return None
    symbol = (payload.get("info") or {}).get("symbol") or ""
    ts_raw = (
        payload.get("lastUpdateTime")
        or (payload.get("metadata") or {}).get("lastUpdateTime")
    )
    if not symbol or not ts_raw or last <= 0:
        return None
    try:
        asof = datetime.strptime(ts_raw.strip(), TS_FORMAT).replace(tzinfo=IST)
    except ValueError:
        return None
    return Quote(symbol=symbol, last=last, asof=asof, received_at=received_at,
                 kind=Kind.EQUITY)


class NSELiveProvider:
    name = "nse_live"
    delayed = False  # near-real-time when the market is open

    def __init__(self, throttle_seconds: float = 2.5, timeout: float = 10.0,
                 max_consecutive_failures: int = 5, cooldown_seconds: float = 600.0):
        self.throttle_seconds = throttle_seconds
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.cooldown_seconds = cooldown_seconds
        self._session = None
        self._last_request = 0.0
        self._failures = 0
        self._down_until = 0.0

    # -- plumbing ------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._last_request + self.throttle_seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _warmup(self):
        import requests

        s = requests.Session()
        s.headers.update(HEADERS)
        s.get(BASE, timeout=self.timeout)  # sets the cookies the API expects
        return s

    def available(self) -> bool:
        return time.monotonic() >= self._down_until

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_consecutive_failures:
            self._down_until = time.monotonic() + self.cooldown_seconds
            self._failures = 0
            self._session = None  # force fresh cookies after cooldown

    # -- API -----------------------------------------------------------
    def live_quote(self, symbol: str) -> Quote | None:
        if not self.available():
            return None
        try:
            if self._session is None:
                self._session = self._warmup()
            self._throttle()
            resp = self._session.get(
                QUOTE_URL, params={"symbol": symbol}, timeout=self.timeout
            )
            if resp.status_code in (401, 403):
                self._session = self._warmup()  # cookies expired; one retry
                self._throttle()
                resp = self._session.get(
                    QUOTE_URL, params={"symbol": symbol}, timeout=self.timeout
                )
            resp.raise_for_status()
            quote = parse_quote_payload(resp.json(), datetime.now(IST))
        except Exception:
            self._record_failure()
            return None
        if quote is None:
            self._record_failure()
            return None
        self._failures = 0
        return quote
