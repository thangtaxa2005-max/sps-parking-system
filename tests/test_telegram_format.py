"""Synchronous rendering tests for Telegram vehicle summaries."""

import sqlite3

from backend import api_core
from backend.cache import CacheState
from backend.telegram_bot import format_event_history, format_vehicle_list


def test_vehicle_with_two_cards_appears_once(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    user_id = api_core.create_resident_user(
        db_connection, cache,
        unit_id=unit_id, username="telegram-resident", password="secret1",
    )
    car_id = int(db_connection.execute(
        "SELECT id FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()[0])
    db_connection.execute(
        """INSERT INTO credentials (cred_type, value, vehicle_id, status, issued_at)
           VALUES ('rfid', 'UID-CAR-2', ?, 'active', '2026-10-11T14:00:00')""",
        (car_id,),
    )
    db_connection.commit()

    result = format_vehicle_list(api_core.list_user_vehicles(db_connection, user_id))

    assert result.count("CAR-1 —") == 1
    assert "UID-CAR: active" in result
    assert "UID-CAR-2: active" in result
    assert result.count("MOTO-1 —") == 1


def test_event_history_uses_readable_local_time() -> None:
    rows = [{
        "ts": "2026-10-11T14:35:42.398497",
        "plate": "29B155555", "gate": "in", "result": "allow",
        "reason_code": "ok",
    }]

    assert format_event_history(rows) == (
        "11/10/2026 14:35:42 — 29B155555 — Vào: cho phép (ok)"
    )
    assert format_event_history([]) == "Chưa có lịch sử."
