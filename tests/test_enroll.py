"""Unit tests for the 60-second card-enrollment backend flow."""

import json
from datetime import datetime, timedelta

import pytest

from backend import api_core
from tests.conftest import add_vehicle


def _request(connection, *, status: str = "pending", plate: str = "NEW-1") -> int:
    unit_id = connection.execute(
        "SELECT id FROM units WHERE unit_code = 'TEST-UNIT'"
    ).fetchone()["id"]
    cursor = connection.execute(
        """
        INSERT INTO registration_requests
            (unit_id, plate, vehicle_type, plan_type, status, created_at)
        VALUES (?, ?, 'car', 'year', ?, ?)
        """,
        (unit_id, plate, status, datetime.now().isoformat()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def test_set_and_get_enroll_state(db_connection, cache):
    request_id = _request(db_connection, status="approved_waiting_card")
    state = api_core.set_enroll_state(
        db_connection, cache, gate="in", request_id=request_id
    )
    assert state["request_id"] == request_id
    assert state["vehicle_id"] is None
    assert api_core.get_enroll_state(db_connection) == state


def test_set_enroll_invalid_gate(db_connection, cache):
    with pytest.raises(ValueError, match="Cổng"):
        api_core.set_enroll_state(db_connection, cache, gate="side", vehicle_id=1)


def test_set_enroll_invalid_duration(db_connection, cache):
    with pytest.raises(ValueError, match="10 đến 300"):
        api_core.set_enroll_state(
            db_connection, cache, gate="in", duration_s=9, vehicle_id=1
        )


def test_set_enroll_requires_request_or_vehicle(db_connection, cache):
    with pytest.raises(ValueError, match="đúng một"):
        api_core.set_enroll_state(db_connection, cache, gate="in")


def test_set_enroll_rejects_both_ids(db_connection, cache):
    request_id = _request(db_connection, status="approved_waiting_card")
    with pytest.raises(ValueError, match="đúng một"):
        api_core.set_enroll_state(
            db_connection, cache, gate="in", request_id=request_id, vehicle_id=1
        )


def test_set_enroll_request_must_be_approved(db_connection, cache):
    request_id = _request(db_connection)
    with pytest.raises(ValueError, match="chưa được duyệt"):
        api_core.set_enroll_state(db_connection, cache, gate="in", request_id=request_id)


def test_set_enroll_vehicle_must_exist(db_connection, cache):
    with pytest.raises(ValueError, match="Không tìm thấy xe"):
        api_core.set_enroll_state(db_connection, cache, gate="in", vehicle_id=999)


def test_get_enroll_returns_none_when_expired(db_connection, cache):
    expired = (datetime.now() - timedelta(seconds=1)).isoformat()
    db_connection.execute(
        "INSERT INTO settings (key, value) VALUES ('enroll_mode', ?)",
        (json.dumps({"gate": "in", "expires_at": expired}),),
    )
    db_connection.commit()
    assert api_core.get_enroll_state(db_connection) is None
    assert db_connection.execute(
        "SELECT 1 FROM settings WHERE key = 'enroll_mode'"
    ).fetchone() is None


def test_clear_enroll_state(db_connection, cache):
    api_core.set_enroll_state(db_connection, cache, gate="in", vehicle_id=1)
    api_core.clear_enroll_state(db_connection, cache)
    assert api_core.get_enroll_state(db_connection) is None


def test_attach_from_request_creates_vehicle_and_credential(db_connection, cache):
    request_id = _request(db_connection, status="approved_waiting_card")
    api_core.set_enroll_state(db_connection, cache, gate="in", request_id=request_id)
    result = api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-NEW", gate="in"
    )
    assert result["created_vehicle"] is True
    assert db_connection.execute(
        "SELECT id FROM vehicles WHERE plate = 'NEW-1'"
    ).fetchone() is not None
    assert db_connection.execute(
        "SELECT vehicle_id FROM credentials WHERE value = 'UID-NEW'"
    ).fetchone() is not None


def test_attach_from_request_marks_request_active(db_connection, cache):
    request_id = _request(db_connection, status="approved_waiting_card")
    api_core.set_enroll_state(db_connection, cache, gate="in", request_id=request_id)
    api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-NEW", gate="in"
    )
    assert db_connection.execute(
        "SELECT status FROM registration_requests WHERE id = ?", (request_id,)
    ).fetchone()["status"] == "active"


def test_attach_from_vehicle_marks_old_credentials_lost(db_connection, cache):
    vehicle_id, _, _ = add_vehicle(
        db_connection, plate="REPLACE-1", vehicle_type="car", uid="UID-OLD"
    )
    api_core.set_enroll_state(db_connection, cache, gate="in", vehicle_id=vehicle_id)
    result = api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-NEW", gate="in"
    )
    assert result["vehicle_id"] == vehicle_id
    assert db_connection.execute(
        "SELECT status FROM credentials WHERE value = 'UID-OLD'"
    ).fetchone()["status"] == "lost"


def test_attach_wrong_gate_returns_none(db_connection, cache):
    api_core.set_enroll_state(db_connection, cache, gate="in", vehicle_id=1)
    assert api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-WRONG-GATE", gate="out"
    ) is None


def test_attach_expired_returns_none(db_connection, cache):
    expired = (datetime.now() - timedelta(seconds=1)).isoformat()
    db_connection.execute(
        "INSERT INTO settings (key, value) VALUES ('enroll_mode', ?)",
        (json.dumps({"gate": "in", "vehicle_id": 1, "request_id": None, "expires_at": expired}),),
    )
    db_connection.commit()
    assert api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-EXPIRED", gate="in"
    ) is None


def test_attach_no_enroll_returns_none(db_connection, cache):
    assert api_core.attach_credential_from_enroll(
        db_connection, cache, uid="UID-NONE", gate="in"
    ) is None
