"""Tests for vehicle and credential management in the web application."""

import sqlite3
from datetime import date, timedelta

import pytest
from flask.testing import FlaskClient

from backend import api_core
from backend.cache import CacheState


def _vehicle_id(connection: sqlite3.Connection, plate: str = "CAR-1") -> int:
    row = connection.execute(
        "SELECT id FROM vehicles WHERE plate = ?", (plate,)
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _credential_id(connection: sqlite3.Connection, uid: str = "UID-CAR") -> int:
    row = connection.execute(
        "SELECT id FROM credentials WHERE value = ?", (uid,)
    ).fetchone()
    assert row is not None
    return int(row["id"])


def test_list_vehicles_with_credentials_returns_all(
    db_connection: sqlite3.Connection,
) -> None:
    results = api_core.list_vehicles_with_credentials(db_connection)

    assert [item["vehicle"]["plate"] for item in results] == ["CAR-1", "MOTO-1"]
    assert all(len(item["credentials"]) == 1 for item in results)


def test_list_vehicles_with_credentials_includes_unit_code(
    db_connection: sqlite3.Connection,
) -> None:
    results = api_core.list_vehicles_with_credentials(db_connection)

    assert all(item["unit_code"] == "TEST-UNIT" for item in results)


def test_extend_vehicle_plan_from_null(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = NULL WHERE plate = 'CAR-1'"
    )
    db_connection.commit()

    expiry = api_core.extend_vehicle_plan(db_connection, cache, "CAR-1", 1)

    assert expiry == api_core.add_months(date.today(), 1)


def test_extend_vehicle_plan_from_future(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    current_expiry = date.today() + timedelta(days=90)
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = ? WHERE plate = 'CAR-1'",
        (current_expiry.isoformat(),),
    )
    db_connection.commit()

    expiry = api_core.extend_vehicle_plan(db_connection, cache, "CAR-1", 3)

    assert expiry == api_core.add_months(current_expiry, 3)


def test_extend_vehicle_plan_from_past(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    db_connection.execute(
        "UPDATE vehicles SET plan_expires_at = '2000-01-01' WHERE plate = 'CAR-1'"
    )
    db_connection.commit()

    expiry = api_core.extend_vehicle_plan(db_connection, cache, "CAR-1", 1)

    assert expiry == api_core.add_months(date.today(), 1)


def test_extend_vehicle_plan_zero_months_raises(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    with pytest.raises(ValueError, match="lớn hơn 0"):
        api_core.extend_vehicle_plan(db_connection, cache, "CAR-1", 0)


def test_add_months_end_of_month() -> None:
    assert api_core.add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert api_core.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_vehicles_page_requires_login(web_client: FlaskClient) -> None:
    response = web_client.get("/vehicles")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_vehicles_page_shows_vehicles(
    authenticated_client: FlaskClient,
) -> None:
    response = authenticated_client.get("/vehicles")
    content = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "CAR-1" in content
    assert "MOTO-1" in content
    assert "Xe và thẻ" in content


def test_lock_credential_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    response = authenticated_client.post(
        f"/vehicles/{_vehicle_id(db_connection)}/credentials/"
        f"{_credential_id(db_connection)}/lock"
    )
    credential = db_connection.execute(
        "SELECT status FROM credentials WHERE value = 'UID-CAR'"
    ).fetchone()

    assert response.status_code == 302
    assert credential is not None
    assert credential["status"] == "locked"


def test_mark_lost_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    response = authenticated_client.post(
        f"/vehicles/{_vehicle_id(db_connection)}/credentials/"
        f"{_credential_id(db_connection)}/mark-lost"
    )
    credential = db_connection.execute(
        "SELECT status FROM credentials WHERE value = 'UID-CAR'"
    ).fetchone()

    assert response.status_code == 302
    assert credential is not None
    assert credential["status"] == "lost"


def test_extend_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    response = authenticated_client.post(
        f"/vehicles/{_vehicle_id(db_connection)}/extend",
        data={"months": "3"},
    )
    vehicle = db_connection.execute(
        "SELECT plan_expires_at FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()

    assert response.status_code == 302
    assert vehicle is not None
    assert vehicle["plan_expires_at"] != "2099-12-31"


def test_start_enroll_for_vehicle(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    response = authenticated_client.post(
        f"/enroll/start",
        data={"vehicle_id": str(_vehicle_id(db_connection)), "gate": "in", "next": "vehicles"},
    )

    assert response.status_code == 302
    state = api_core.get_enroll_state(db_connection)
    assert state is not None
    assert state["vehicle_id"] == _vehicle_id(db_connection)


def test_history_and_alert_pages_require_manager(authenticated_client: FlaskClient) -> None:
    assert authenticated_client.get("/history").status_code == 200
    assert authenticated_client.get("/alerts").status_code == 200


def test_manual_occupancy_adjustment_records_actor(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
) -> None:
    vehicle_id = _vehicle_id(db_connection)
    response = authenticated_client.post(
        f"/vehicles/{vehicle_id}/occupancy", data={"in_lot": "1"}
    )
    vehicle = db_connection.execute(
        "SELECT in_lot FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()
    event = db_connection.execute(
        "SELECT reason_code, ocr_plate FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert response.status_code == 302
    assert vehicle["in_lot"] == 1
    assert event["reason_code"] == "manual_adjust"
    assert event["ocr_plate"] == "web-test-manager"


def test_settings_update_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    response = authenticated_client.post(
        "/settings",
        data={
            "capacity_total": "25",
            "default_quota_car": "2",
            "default_quota_moto": "3",
            "ocr_threshold": "0.75",
        },
    )
    assert response.status_code == 302
    settings = api_core.get_settings(db_connection)
    assert settings["capacity_total"] == "25"
    assert settings["ocr_threshold"] == "0.75"


def test_settings_reject_invalid_ocr_threshold(authenticated_client: FlaskClient) -> None:
    response = authenticated_client.post(
        "/settings",
        data={
            "capacity_total": "20",
            "default_quota_car": "1",
            "default_quota_moto": "1",
            "ocr_threshold": "2",
        },
    )
    assert response.status_code == 302


def test_remote_gate_open_queues_command(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    response = authenticated_client.post("/gate/open", data={"gate": "in"})
    command = db_connection.execute(
        "SELECT gate, command, actor, sent_at FROM gate_commands ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert response.status_code == 302
    assert command["gate"] == "in"
    assert command["command"] == "open_command"
    assert command["actor"] == "web-test-manager"
    assert command["sent_at"] is None


def test_create_resident_and_generate_telegram_code(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    response = authenticated_client.post(
        "/accounts/resident",
        data={"unit_id": str(unit_id), "username": "resident-1", "password": "secret1"},
    )
    assert response.status_code == 302
    user_id = int(db_connection.execute("SELECT id FROM users WHERE username = 'resident-1'").fetchone()[0])
    response = authenticated_client.post(f"/accounts/{user_id}/telegram-code")
    row = db_connection.execute("SELECT link_code, link_code_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
    assert response.status_code == 302
    assert row["link_code"]
    assert row["link_code_expires_at"]


def test_manager_can_create_unit_from_accounts_page(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    response = authenticated_client.post(
        "/accounts/unit", data={"unit_code": "A-101"},
    )
    unit = db_connection.execute(
        "SELECT unit_code, quota_car, quota_moto FROM units WHERE unit_code = 'A-101'"
    ).fetchone()
    assert response.status_code == 302
    assert unit is not None
    assert unit["quota_car"] == 1
    assert unit["quota_moto"] == 1
    assert "A-101" in authenticated_client.get("/accounts").get_data(as_text=True)


def test_manager_can_queue_telegram_test_notification_for_linked_resident(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    resident_id = api_core.create_resident_user(
        db_connection, cache, unit_id=unit_id,
        username="linked-resident", password="secret1",
    )
    db_connection.execute(
        "UPDATE users SET telegram_chat_id = 'test-chat' WHERE id = ?",
        (resident_id,),
    )
    db_connection.commit()

    response = authenticated_client.post(
        f"/accounts/{resident_id}/test-notification"
    )
    row = db_connection.execute(
        "SELECT recipient_user_id, level, content, sent_telegram "
        "FROM notifications ORDER BY id DESC LIMIT 1"
    ).fetchone()

    assert response.status_code == 302
    assert row["recipient_user_id"] == resident_id
    assert row["level"] == "info"
    assert "thông báo thử" in row["content"]
    assert row["sent_telegram"] == 0


def test_test_notification_requires_linked_resident(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    resident_id = api_core.create_resident_user(
        db_connection, cache, unit_id=unit_id,
        username="unlinked-resident", password="secret1",
    )

    assert authenticated_client.post(
        f"/accounts/{resident_id}/test-notification"
    ).status_code == 302
    assert db_connection.execute(
        "SELECT COUNT(*) FROM notifications"
    ).fetchone()[0] == 0


def test_telegram_link_code_is_single_use(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    user_id = api_core.create_resident_user(
        db_connection, cache, unit_id=unit_id, username="resident-link", password="secret1"
    )
    code = api_core.create_telegram_link_code(db_connection, user_id)
    linked = api_core.link_telegram_chat(db_connection, code, "chat-1")
    assert linked["telegram_chat_id"] == "chat-1"
    with pytest.raises(ValueError, match="không hợp lệ"):
        api_core.link_telegram_chat(db_connection, code, "chat-2")


def test_telegram_chat_cannot_link_two_residents(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    unit_id = int(db_connection.execute("SELECT id FROM units LIMIT 1").fetchone()[0])
    first = api_core.create_resident_user(db_connection, cache, unit_id=unit_id, username="resident-a", password="secret1")
    second = api_core.create_resident_user(db_connection, cache, unit_id=unit_id, username="resident-b", password="secret1")
    first_code = api_core.create_telegram_link_code(db_connection, first)
    second_code = api_core.create_telegram_link_code(db_connection, second)
    api_core.link_telegram_chat(db_connection, first_code, "chat-unique")
    with pytest.raises(ValueError, match="đã liên kết"):
        api_core.link_telegram_chat(db_connection, second_code, "chat-unique")
