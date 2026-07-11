"""Quote recorder: dedupe, staleness accounting, fallback, calendar gating."""

from datetime import datetime, timedelta

from atis.mktcalendar import IST
from atis.models import Kind, Quote
from atis.recorder import QuoteRecorder
from tests.conftest import T0


class FakeProvider:
    name = "fake"
    delayed = False

    def __init__(self, quotes: dict[str, list[Quote | None]]):
        self._quotes = {s: list(qs) for s, qs in quotes.items()}

    def available(self) -> bool:
        return True

    def live_quote(self, symbol: str) -> Quote | None:
        qs = self._quotes.get(symbol, [])
        return qs.pop(0) if qs else None


def q(symbol: str, last: float, asof: datetime, received: datetime | None = None) -> Quote:
    return Quote(symbol=symbol, last=last, asof=asof,
                 received_at=received or asof, kind=Kind.EQUITY)


def make_recorder(conn, audit, calendar, provider, fallback=None, clock=None):
    return QuoteRecorder(
        conn, audit, calendar, ["RELIANCE"], provider, fallback,
        clock=clock or (lambda: T0), sleep_fn=lambda s: None,
    )


def test_records_and_dedupes(conn, audit, calendar):
    same_asof = T0
    provider = FakeProvider({"RELIANCE": [
        q("RELIANCE", 100.0, same_asof),
        q("RELIANCE", 100.0, same_asof),                       # unchanged tick
        q("RELIANCE", 100.5, same_asof + timedelta(minutes=1)),
    ]})
    rec = make_recorder(conn, audit, calendar, provider)
    summary = rec.run_session(force=True, once=True)
    rec.cycle(summary)
    rec.cycle(summary)

    assert summary.rows_inserted == 2
    assert summary.duplicates_skipped == 1
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 2


def test_stale_quotes_are_counted_not_hidden(conn, audit, calendar):
    stale = q("RELIANCE", 100.0, asof=T0 - timedelta(minutes=20), received=T0)
    rec = make_recorder(conn, audit, calendar, FakeProvider({"RELIANCE": [stale]}))
    summary = rec.run_session(force=True, once=True)
    assert summary.rows_inserted == 1
    assert summary.stale_quotes == 1


def test_fallback_used_when_primary_fails(conn, audit, calendar):
    primary = FakeProvider({"RELIANCE": [None]})
    fallback = FakeProvider({"RELIANCE": [q("RELIANCE", 99.0, T0)]})
    fallback.name, fallback.delayed = "fb", True
    rec = make_recorder(conn, audit, calendar, primary, fallback)
    summary = rec.run_session(force=True, once=True)
    assert summary.rows_inserted == 1
    assert summary.delayed_quotes == 1
    row = conn.execute("SELECT source, is_delayed FROM quotes").fetchone()
    assert row["source"] == "fb" and row["is_delayed"] == 1


def test_both_fail_counts_error(conn, audit, calendar):
    rec = make_recorder(conn, audit, calendar,
                        FakeProvider({}), FakeProvider({}))
    summary = rec.run_session(force=True, once=True)
    assert summary.errors == 1
    assert summary.rows_inserted == 0


def test_skips_non_trading_day(conn, audit, calendar):
    saturday = datetime(2026, 7, 11, 10, 0, tzinfo=IST)
    rec = make_recorder(conn, audit, calendar, FakeProvider({}),
                        clock=lambda: saturday)
    summary = rec.run_session()
    assert "not a trading day" in summary.skipped_reason
    assert summary.cycles == 0


def test_skips_after_close(conn, audit, calendar):
    late = datetime(2026, 7, 10, 16, 0, tzinfo=IST)
    rec = make_recorder(conn, audit, calendar, FakeProvider({}),
                        clock=lambda: late)
    summary = rec.run_session()
    assert "closed" in summary.skipped_reason


def test_heartbeat_written(conn, audit, calendar):
    rec = make_recorder(conn, audit, calendar,
                        FakeProvider({"RELIANCE": [q("RELIANCE", 100.0, T0)]}))
    rec.run_session(force=True, once=True)
    hb = conn.execute(
        "SELECT value FROM meta WHERE key='recorder_heartbeat'"
    ).fetchone()
    assert hb is not None


def test_archive_intraday_idempotent(conn, audit):
    from atis.recorder import archive_intraday

    class FakeBars:
        name = "fake_bars"

        def intraday_bars(self, symbol):
            return [
                {"ts": "2026-07-10T09:15:00+05:30", "open": 100.0, "high": 101.0,
                 "low": 99.5, "close": 100.5, "volume": 1000},
                {"ts": "2026-07-10T09:16:00+05:30", "open": 100.5, "high": 100.8,
                 "low": 100.2, "close": 100.6, "volume": 800},
            ]

    s1 = archive_intraday(conn, audit, ["RELIANCE"], FakeBars())
    assert s1 == {"symbols_ok": 1, "symbols_empty": 0, "rows_inserted": 2}
    # re-run: same bars, zero new rows
    s2 = archive_intraday(conn, audit, ["RELIANCE"], FakeBars())
    assert s2["rows_inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM ohlcv_1min").fetchone()[0] == 2


def test_nse_payload_parser():
    from atis.data.nse_live import parse_quote_payload

    received = datetime(2026, 7, 10, 15, 30, 5, tzinfo=IST)
    payload = {
        "info": {"symbol": "RELIANCE"},
        "priceInfo": {"lastPrice": 1307.8},
        "metadata": {"lastUpdateTime": "10-Jul-2026 15:30:00"},
    }
    quote = parse_quote_payload(payload, received)
    assert quote is not None
    assert quote.symbol == "RELIANCE"
    assert quote.last == 1307.8
    assert quote.asof == datetime(2026, 7, 10, 15, 30, tzinfo=IST)

    assert parse_quote_payload({}, received) is None
    assert parse_quote_payload({"priceInfo": {"lastPrice": 0}}, received) is None
