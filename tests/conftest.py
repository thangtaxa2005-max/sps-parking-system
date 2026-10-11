"""Shared in-memory database fixtures and sample data helpers."""

import sqlite3
from collections.abc import Iterator
from datetime import date

import pytest
from flask.testing import FlaskClient

from backend import api_core
from backend.cache import CacheState
from backend.db import initialize_schema


def add_vehicle(
    connection: sqlite3.Connection,
    *,
    plate: str,
    vehicle_type: str,
    in_lot: bool = False,
    plan_expires_at: date | None = date(2099, 12, 31),
    credential_status: str = "active",
    uid: str | None = None,
) -> tuple[int, int, str]:
    """Insert one vehicle and its RFID credential for a test."""
    unit_id = int(
        connection.execute(
            "SELECT id FROM units WHERE unit_code = 'TEST-UNIT'"
        ).fetchone()["id"]
    )
    vehicle_cursor = connection.execute(
        """
        INSERT INTO vehicles (
            plate, vehicle_type, unit_id, plan_expires_at, in_lot
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            plate,
            vehicle_type,
            unit_id,
            plan_expires_at.isoformat() if plan_expires_at is not None else None,
            int(in_lot),
        ),
    )
    vehicle_id = int(vehicle_cursor.lastrowid)
    credential_uid = uid or f"UID-{plate}"
    credential_cursor = connection.execute(
        """
        INSERT INTO credentials (cred_type, value, vehicle_id, status, issued_at)
        VALUES ('rfid', ?, ?, ?, '2026-01-01T00:00:00')
        """,
        (credential_uid, vehicle_id, credential_status),
    )
    connection.commit()
    return vehicle_id, int(credential_cursor.lastrowid), credential_uid


@pytest.fixture
def db_connection() -> Iterator[sqlite3.Connection]:
    """Create a new in-memory database with a resident unit and sample vehicles."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute("INSERT INTO units (unit_code) VALUES ('TEST-UNIT')")
    add_vehicle(connection, plate="CAR-1", vehicle_type="car", uid="UID-CAR")
    add_vehicle(connection, plate="MOTO-1", vehicle_type="moto", uid="UID-MOTO")
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def cache(db_connection: sqlite3.Connection) -> CacheState:
    """Return an initialized cache for the fixture database."""
    state = CacheState(db_connection)
    state.refresh_from_db()
    return state


@pytest.fixture
def web_client(
    db_connection: sqlite3.Connection, cache: CacheState
) -> Iterator[FlaskClient]:
    """Provide a Flask test client backed by the in-memory test database."""
    from backend.web import create_app

    api_core.create_manager_user(
        db_connection,
        cache,
        username="web-test-manager",
        password="test-password",
    )
    app = create_app(database_connection=db_connection)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def authenticated_client(web_client: FlaskClient) -> FlaskClient:
    """Return the web test client after manager login."""
    response = web_client.post(
        "/login",
        data={"username": "web-test-manager", "password": "test-password"},
    )
    assert response.status_code == 302
    return web_client
