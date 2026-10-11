"""Tests for registration-request management in the web application."""

import sqlite3
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from backend import api_core
from backend.cache import CacheState
from backend.db import connect_database, initialize_schema
from backend.web import create_app


def _test_unit_id(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM units WHERE unit_code = 'TEST-UNIT'"
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _create_request(
    connection: sqlite3.Connection,
    cache: CacheState,
    *,
    plate: str = "29B12345",
) -> int:
    return api_core.create_registration_request(
        connection,
        cache,
        unit_id=_test_unit_id(connection),
        plate=plate,
        vehicle_type="moto",
        plan_type="month",
    )


def test_create_registration_request_success(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    request_id = _create_request(db_connection, cache)

    registration_request = api_core.get_registration_request(
        db_connection, request_id
    )
    assert registration_request is not None
    assert registration_request["status"] == "pending"
    assert registration_request["unit_code"] == "TEST-UNIT"


def test_create_registration_request_invalid_vehicle_type(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    with pytest.raises(ValueError, match="Loại xe"):
        api_core.create_registration_request(
            db_connection,
            cache,
            unit_id=_test_unit_id(db_connection),
            plate="29B12345",
            vehicle_type="truck",
            plan_type="month",
        )


def test_create_registration_request_invalid_unit(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    with pytest.raises(ValueError, match="Không tìm thấy căn hộ"):
        api_core.create_registration_request(
            db_connection,
            cache,
            unit_id=-1,
            plate="29B12345",
            vehicle_type="moto",
            plan_type="month",
        )


def test_list_registration_requests_filter_by_status(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    pending_id = _create_request(db_connection, cache, plate="29B12345")
    approved_id = _create_request(db_connection, cache, plate="30A99999")
    api_core.approve_registration_request(db_connection, cache, approved_id)

    pending = api_core.list_registration_requests(
        db_connection, status="pending"
    )
    approved = api_core.list_registration_requests(
        db_connection, status="approved_waiting_card"
    )
    assert [int(row["id"]) for row in pending] == [pending_id]
    assert [int(row["id"]) for row in approved] == [approved_id]


def test_approve_pending_request(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    request_id = _create_request(db_connection, cache)

    api_core.approve_registration_request(db_connection, cache, request_id)

    request_row = api_core.get_registration_request(db_connection, request_id)
    assert request_row is not None
    assert request_row["status"] == "approved_waiting_card"


def test_approve_non_pending_raises(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    request_id = _create_request(db_connection, cache)
    api_core.approve_registration_request(db_connection, cache, request_id)

    with pytest.raises(ValueError, match="đang chờ duyệt"):
        api_core.approve_registration_request(db_connection, cache, request_id)


def test_reject_pending_request_with_reason(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    request_id = _create_request(db_connection, cache)

    api_core.reject_registration_request(
        db_connection, cache, request_id, "  Thiếu giấy tờ  "
    )

    request_row = api_core.get_registration_request(db_connection, request_id)
    assert request_row is not None
    assert request_row["status"] == "rejected"
    assert request_row["reject_reason"] == "Thiếu giấy tờ"


def test_reject_empty_reason_raises(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    request_id = _create_request(db_connection, cache)

    with pytest.raises(ValueError, match="lý do từ chối"):
        api_core.reject_registration_request(db_connection, cache, request_id, " ")


def test_requests_page_requires_login(web_client: FlaskClient) -> None:
    response = web_client.get("/requests")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_requests_page_shows_pending(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    _create_request(db_connection, cache, plate="29B155555")

    response = authenticated_client.get("/requests")

    assert response.status_code == 200
    assert "29B155555" in response.get_data(as_text=True)


def test_approve_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    request_id = _create_request(db_connection, cache)

    response = authenticated_client.post(f"/requests/{request_id}/approve")

    request_row = api_core.get_registration_request(db_connection, request_id)
    assert response.status_code == 302
    assert request_row is not None
    assert request_row["status"] == "approved_waiting_card"


def test_start_enroll_for_approved_request(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    request_id = _create_request(db_connection, cache)
    api_core.approve_registration_request(db_connection, cache, request_id)

    response = authenticated_client.post(
        "/enroll/start",
        data={"request_id": str(request_id), "gate": "in", "next": "requests"},
    )

    assert response.status_code == 302
    state = api_core.get_enroll_state(db_connection)
    assert state is not None
    assert state["request_id"] == request_id
    assert state["gate"] == "in"


def test_cancel_enroll(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    api_core.set_enroll_state(db_connection, cache, gate="in", vehicle_id=1)

    response = authenticated_client.post(
        "/enroll/clear", data={"next": "requests"}
    )

    assert response.status_code == 302
    assert api_core.get_enroll_state(db_connection) is None


def test_reject_via_http(
    authenticated_client: FlaskClient,
    db_connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    request_id = _create_request(db_connection, cache)

    response = authenticated_client.post(
        f"/requests/{request_id}/reject",
        data={"reason": "Thiếu giấy tờ"},
    )

    request_row = api_core.get_registration_request(db_connection, request_id)
    assert response.status_code == 302
    assert request_row is not None
    assert request_row["status"] == "rejected"
    assert request_row["reject_reason"] == "Thiếu giấy tờ"


def test_reject_persists_across_file_database_connections(tmp_path: Path) -> None:
    """The web route must commit to the same file the bot and WS processes read."""
    database_path = tmp_path / "sps.sqlite3"
    setup = connect_database(database_path)
    try:
        initialize_schema(setup)
        cache = CacheState(setup)
        unit_id = api_core.create_unit(setup, cache, "UNIT-FILE")
        api_core.create_manager_user(
            setup, cache, username="manager-file", password="secret1",
        )
        request_id = api_core.create_registration_request(
            setup, cache, unit_id=unit_id, plate="30A88888",
            vehicle_type="car", plan_type="month",
        )
        app = create_app(database_path=database_path)
        app.config["TESTING"] = True
        with app.test_client() as client:
            login = client.post(
                "/login", data={"username": "manager-file", "password": "secret1"},
            )
            assert login.status_code == 302
            response = client.post(
                f"/requests/{request_id}/reject",
                data={"reason": "Yêu cầu thử Telegram"},
            )
            assert response.status_code == 302
        reader = connect_database(database_path)
        try:
            row = reader.execute(
                "SELECT status, reject_reason FROM registration_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            assert row["status"] == "rejected"
            assert row["reject_reason"] == "Yêu cầu thử Telegram"
        finally:
            reader.close()
        app.config["SHARED_CACHE"].connection.close()
    finally:
        setup.close()
