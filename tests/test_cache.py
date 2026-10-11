"""Cache refresh tests."""

import sqlite3

from backend.cache import CacheState
from tests.conftest import add_vehicle


def test_cache_free_total(db_connection: sqlite3.Connection) -> None:
    for index in range(5):
        add_vehicle(
            db_connection,
            plate=f"OCCUPIED-{index}",
            vehicle_type="car" if index % 2 == 0 else "moto",
            in_lot=True,
        )

    cache = CacheState(db_connection)
    cache.refresh_from_db()

    assert cache.free_total == 15
    assert cache.get_slots() == 15


def test_cache_refresh_after_lock(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'locked' WHERE value = 'UID-CAR'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    assert cache.credentials["UID-CAR"].status == "locked"


def test_cache_refresh_after_confirm_pass(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    from backend.events import confirm_pass

    credential_id = int(
        db_connection.execute(
            "SELECT id FROM credentials WHERE value = 'UID-CAR'"
        ).fetchone()["id"]
    )
    free_before = cache.free_total

    from datetime import datetime

    confirm_pass(
        "in",
        credential_id,
        datetime(2026, 10, 6, 12, 0, 0),
        db_connection,
        cache,
    )

    assert cache.free_total == free_before - 1
    assert cache.credentials["UID-CAR"].in_lot is True
