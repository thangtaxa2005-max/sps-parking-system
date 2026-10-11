"""Alert acknowledgment remains independent of Telegram delivery."""

import sqlite3

import pytest
from flask.testing import FlaskClient

from backend import api_core
from backend.cache import CacheState
from backend.db import initialize_schema


def _insert_alert(conn: sqlite3.Connection, *, level: str = "alert") -> int:
    manager_id = int(
        conn.execute("SELECT id FROM users WHERE username = 'web-test-manager'").fetchone()[0]
    )
    cursor = conn.execute(
        """INSERT INTO notifications
           (recipient_user_id, level, content, created_at)
           VALUES (?, ?, 'Cần kiểm tra biển số', '2026-10-11T14:00:00')""",
        (manager_id, level),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_delivered_alert_stays_open_until_manager_resolves_it(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    alert_id = _insert_alert(db_connection)
    api_core.mark_notification_sent(db_connection, alert_id)

    assert api_core.count_open_alerts(db_connection) == 1
    assert [row["id"] for row in api_core.list_open_alerts(db_connection)] == [alert_id]
    assert "Cần kiểm tra biển số" in authenticated_client.get("/alerts").get_data(as_text=True)

    response = authenticated_client.post(f"/alerts/{alert_id}/resolve")
    row = db_connection.execute(
        "SELECT sent_telegram, resolved_at, resolved_by_user_id "
        "FROM notifications WHERE id = ?", (alert_id,)
    ).fetchone()

    assert response.status_code == 302
    assert row["sent_telegram"] == 1
    assert row["resolved_at"] is not None
    assert row["resolved_by_user_id"] is not None
    assert api_core.count_open_alerts(db_connection) == 0
    assert "Cần kiểm tra biển số" not in authenticated_client.get("/alerts").get_data(as_text=True)


def test_only_open_alerts_can_be_resolved(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    info_id = _insert_alert(db_connection, level="info")
    manager_id = int(
        db_connection.execute("SELECT id FROM users WHERE username = 'web-test-manager'").fetchone()[0]
    )
    with pytest.raises(ValueError, match="không tồn tại"):
        api_core.resolve_alert(db_connection, info_id, manager_id)
    assert authenticated_client.post(f"/alerts/{info_id}/resolve").status_code == 302
    assert api_core.count_open_alerts(db_connection) == 0


def test_resident_copy_does_not_inflate_manager_alert_count(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    manager_id = api_core.create_manager_user(
        db_connection, cache, username="web-test-manager", password="test-password"
    )
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    resident_id = api_core.create_resident_user(
        db_connection, cache, unit_id=unit_id,
        username="alert-resident", password="test-password",
    )
    db_connection.executemany(
        """INSERT INTO notifications
           (recipient_user_id, level, content, created_at)
           VALUES (?, 'alert', 'Cùng sự kiện', '2026-10-11T14:00:00')""",
        [(manager_id,), (resident_id,)],
    )
    db_connection.commit()

    assert api_core.count_open_alerts(db_connection) == 1
    assert len(api_core.list_open_alerts(db_connection)) == 1
    resident_alert_id = int(db_connection.execute(
        "SELECT id FROM notifications WHERE recipient_user_id = ?", (resident_id,)
    ).fetchone()[0])
    with pytest.raises(ValueError, match="không tồn tại"):
        api_core.resolve_alert(db_connection, resident_alert_id, manager_id)


def test_alert_correction_updates_vehicle_audit_and_gate_queue(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    alert_id = _insert_alert(db_connection)
    vehicle_id = int(db_connection.execute(
        "SELECT id FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()[0])

    response = authenticated_client.post(
        f"/alerts/{alert_id}/correct-and-open",
        data={"vehicle_id": str(vehicle_id), "in_lot": "1", "gate": "in"},
    )

    assert response.status_code == 302
    assert db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()[0] == 1
    event = db_connection.execute(
        "SELECT gate, reason_code, ocr_plate FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(event) == ("in", "manual_adjust", "web-test-manager")
    command = db_connection.execute(
        "SELECT gate, actor, sent_at FROM gate_commands ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(command) == ("in", "web-test-manager", None)
    assert api_core.count_open_alerts(db_connection) == 0


def test_invalid_alert_correction_changes_nothing(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    alert_id = _insert_alert(db_connection)
    vehicle_id = int(db_connection.execute("SELECT id FROM vehicles LIMIT 1").fetchone()[0])
    before = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()[0]

    response = authenticated_client.post(
        f"/alerts/{alert_id}/correct-and-open",
        data={"vehicle_id": str(vehicle_id), "in_lot": "1", "gate": "unknown"},
    )

    assert response.status_code == 302
    assert db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()[0] == before
    assert db_connection.execute("SELECT COUNT(*) FROM gate_commands").fetchone()[0] == 0
    assert api_core.count_open_alerts(db_connection) == 1


def test_alert_migration_preserves_telegram_state() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT NOT NULL);
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY, recipient_user_id INTEGER NOT NULL,
            level TEXT NOT NULL, content TEXT NOT NULL, image_path TEXT,
            created_at TEXT NOT NULL, sent_telegram INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO users (role) VALUES ('manager');
        INSERT INTO notifications
            (recipient_user_id, level, content, created_at, sent_telegram)
            VALUES (1, 'alert', 'Old alert', '2026-10-11T14:00:00', 1);
        """
    )

    initialize_schema(conn)
    initialize_schema(conn)
    row = conn.execute(
        "SELECT sent_telegram, resolved_at, resolved_by_user_id FROM notifications"
    ).fetchone()
    assert row["sent_telegram"] == 1
    assert row["resolved_at"] is None
    assert row["resolved_by_user_id"] is None
    assert api_core.count_open_alerts(conn) == 1
    conn.close()
