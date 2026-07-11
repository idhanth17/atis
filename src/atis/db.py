"""SQLite storage (WAL mode). One file, one schema, migrations by hand for now.

The audit_log table is append-only, enforced by triggers in the schema itself —
not by convention (SECURITY.md §7).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    category TEXT NOT NULL,
    event TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    config_version TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target_price REAL,
    qty INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    features_hash TEXT NOT NULL DEFAULT '',
    catalysts TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    limit_price REAL,
    trigger_price REAL,
    product TEXT NOT NULL,
    status TEXT NOT NULL,
    reject_reason TEXT,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    ts TEXT NOT NULL,
    costs TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    entry_type TEXT NOT NULL,      -- SEED | MARGIN_BLOCK | MARGIN_RELEASE | PREMIUM | PROCEEDS | REALIZED_PNL | COSTS
    amount REAL NOT NULL,          -- signed cash delta
    ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS order_rate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch REAL NOT NULL,
    day TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_rate_day ON order_rate(day);

CREATE TABLE IF NOT EXISTS breaker_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv_daily (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT NOT NULL,
    asof TEXT NOT NULL,           -- exchange timestamp of the quote
    received_at TEXT NOT NULL,    -- when we received it locally
    last REAL NOT NULL,
    bid REAL,
    ask REAL,
    source TEXT NOT NULL,
    is_delayed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, asof, source)
);
CREATE INDEX IF NOT EXISTS idx_quotes_received ON quotes(received_at);

CREATE TABLE IF NOT EXISTS ohlcv_1min (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,             -- bar start, IST
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, ts, source)
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    margin_blocked REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
