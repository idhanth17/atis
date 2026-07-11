"""The audit log must be append-only — enforced by the schema, not convention."""

import sqlite3

import pytest


def test_audit_write_and_tail(audit):
    audit.log("SYSTEM", "hello", n=1)
    rows = audit.tail(5)
    assert len(rows) == 1
    assert rows[0]["category"] == "SYSTEM"
    assert '"n": 1' in rows[0]["details"]


def test_audit_update_is_blocked(conn, audit):
    audit.log("SYSTEM", "immutable")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET event = 'tampered'")


def test_audit_delete_is_blocked(conn, audit):
    audit.log("SYSTEM", "immutable")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log")
