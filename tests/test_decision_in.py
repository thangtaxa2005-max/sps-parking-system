"""Decision tests for the entry gate."""

from datetime import datetime
import sqlite3

from backend.cache import CacheState
from backend.decision import decide
from tests.conftest import add_vehicle


NOW = datetime(2026, 10, 6, 12, 0, 0)


def test_allow_ok_car(cache: CacheState) -> None:
    result = decide("in", "UID-CAR", NOW, cache)
    assert result.allow is True
    assert result.reason_code == "ok"


def test_allow_ok_moto(cache: CacheState) -> None:
    result = decide("in", "UID-MOTO", NOW, cache)
    assert result.allow is True
    assert result.reason_code == "ok"


def test_deny_card_unknown(cache: CacheState) -> None:
    result = decide("in", "UNKNOWN", NOW, cache)
    assert result.allow is False
    assert result.reason_code == "card_unknown"
    assert result.alert is False


def test_deny_card_locked(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'locked' WHERE value = 'UID-CAR'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.reason_code == "card_locked"
    assert result.alert is True
    assert result.oled_message == "Thẻ đã khóa"


def test_deny_card_lost(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'lost' WHERE value = 'UID-CAR'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.reason_code == "card_lost"
    assert result.alert is True
    assert result.oled_message == "Thẻ đã khóa"


def test_deny_plan_expired(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = '2026-10-05' "
        "WHERE plate = 'CAR-1'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.reason_code == "plan_expired"
    assert result.alert is False


def test_deny_already_in(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute("UPDATE vehicles SET in_lot = 1 WHERE plate = 'CAR-1'")
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.reason_code == "already_in_lot"
    assert result.alert is True


def test_deny_lot_full(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    for index in range(20):
        add_vehicle(
            db_connection,
            plate=f"FULL-{index}",
            vehicle_type="car" if index % 2 == 0 else "moto",
            in_lot=True,
        )
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert cache.free_total == 0
    assert result.reason_code == "lot_full"


def test_thu_tu_uu_tien(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE credentials SET status = 'locked' WHERE value = 'UID-CAR'"
    )
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = '2026-10-05' "
        "WHERE plate = 'CAR-1'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.reason_code == "card_locked"


def test_decide_is_pure(cache: CacheState) -> None:
    credentials_before = cache.credentials.copy()
    vehicles_before = cache.vehicles.copy()
    slots_before = cache.get_slots()

    first = decide("in", "UID-CAR", NOW, cache)
    second = decide("in", "UID-CAR", NOW, cache)

    assert first == second
    assert cache.credentials == credentials_before
    assert cache.vehicles == vehicles_before
    assert cache.get_slots() == slots_before


def test_allow_when_plan_expires_at_is_none(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    """Xe không ghi ngày hết hạn (NULL) → coi như vô thời hạn, cho vào."""
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = NULL WHERE plate = 'CAR-1'"
    )
    db_connection.commit()
    cache.refresh_from_db()

    result = decide("in", "UID-CAR", NOW, cache)
    assert result.allow is True
    assert result.reason_code == "ok"