"""Event recording and occupancy transition tests."""

import sqlite3
from datetime import datetime

import pytest

from backend.cache import CacheState
from backend.events import confirm_pass, record_no_entry


NOW = datetime(2026, 10, 6, 12, 0, 0)


def _credential_id(connection: sqlite3.Connection, uid: str) -> int:
    row = connection.execute(
        "SELECT id FROM credentials WHERE value = ?", (uid,)
    ).fetchone()
    return int(row["id"])


def test_confirm_pass_in(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    free_before = cache.free_total
    confirm_pass(
        "in", _credential_id(db_connection, "UID-CAR"), NOW, db_connection, cache
    )

    row = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()
    event = db_connection.execute("SELECT * FROM events").fetchone()
    assert row["in_lot"] == 1
    assert cache.free_total == free_before - 1
    assert event["result"] == "allow"
    assert event["reason_code"] == "ok"
    assert event["t_tap"] == NOW.isoformat()


def test_confirm_pass_out(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute("UPDATE vehicles SET in_lot = 1 WHERE plate = 'CAR-1'")
    db_connection.commit()
    cache.refresh_from_db()
    free_before = cache.free_total

    confirm_pass(
        "out", _credential_id(db_connection, "UID-CAR"), NOW, db_connection, cache
    )

    row = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()
    assert row["in_lot"] == 0
    assert cache.free_total == free_before + 1


def test_no_entry_does_not_change_in_lot(
    db_connection: sqlite3.Connection,
) -> None:
    credential_id = _credential_id(db_connection, "UID-CAR")
    before = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()["in_lot"]

    record_no_entry("in", credential_id, NOW, db_connection)

    after = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()["in_lot"]
    event = db_connection.execute("SELECT * FROM events").fetchone()
    assert after == before
    assert event["result"] == "deny"
    assert event["reason_code"] == "no_entry"
    assert db_connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


@pytest.mark.parametrize("gate", ["invalid", ""])
def test_event_functions_reject_invalid_gate(
    db_connection: sqlite3.Connection, cache: CacheState, gate: str
) -> None:
    credential_id = _credential_id(db_connection, "UID-CAR")
    with pytest.raises(ValueError):
        confirm_pass(gate, credential_id, NOW, db_connection, cache)
    with pytest.raises(ValueError):
        record_no_entry(gate, credential_id, NOW, db_connection)
