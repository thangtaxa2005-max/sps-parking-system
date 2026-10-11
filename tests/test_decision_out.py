"""Decision tests for the exit gate."""

from datetime import datetime
import sqlite3

from backend.cache import CacheState
from backend.decision import decide


NOW = datetime(2026, 10, 6, 12, 0, 0)


def test_out_allow_ok(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute("UPDATE vehicles SET in_lot = 1 WHERE plate = 'CAR-1'")
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("out", "UID-CAR", NOW, cache)
    assert result.allow is True
    assert result.reason_code == "ok"


def test_out_allow_expired(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE vehicles SET in_lot = 1, plan_expires_at = '2026-10-05' "
        "WHERE plate = 'CAR-1'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("out", "UID-CAR", NOW, cache)
    assert result.allow is True
    assert result.reason_code == "ok"


def test_out_deny_not_in(cache: CacheState) -> None:
    result = decide("out", "UID-CAR", NOW, cache)
    assert result.reason_code == "not_in_lot"
    assert result.alert is True


def test_out_deny_card_locked(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'locked' WHERE value = 'UID-CAR'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("out", "UID-CAR", NOW, cache)
    assert result.reason_code == "card_locked"
    assert result.alert is True


def test_out_deny_card_lost(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'lost' WHERE value = 'UID-CAR'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("out", "UID-CAR", NOW, cache)
    assert result.reason_code == "card_lost"
    assert result.alert is True
