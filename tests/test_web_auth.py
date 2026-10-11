"""Tests for user authentication and the manager web skeleton."""

import sqlite3

import pytest
from flask.testing import FlaskClient

from backend import api_core
from backend.cache import CacheState


def test_create_manager_success(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    user_id = api_core.create_manager_user(
        db_connection,
        cache,
        username="new-manager",
        password="valid-password",
    )

    user = api_core.get_user_by_id(db_connection, user_id)
    assert user is not None
    assert user["role"] == "manager"
    assert cache.users_by_username["new-manager"]["id"] == user_id


def test_create_manager_duplicate_username_raises(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    api_core.create_manager_user(
        db_connection, cache, username="same-name", password="valid-password"
    )

    with pytest.raises(ValueError, match="username already exists"):
        api_core.create_manager_user(
            db_connection, cache, username="same-name", password="valid-password"
        )


def test_create_manager_short_password_raises(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    with pytest.raises(ValueError, match="at least 6 characters"):
        api_core.create_manager_user(
            db_connection, cache, username="short-pass", password="12345"
        )


def test_verify_user_password_correct(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    api_core.create_manager_user(
        db_connection, cache, username="verify-user", password="valid-password"
    )

    user = api_core.verify_user_password(
        db_connection, "verify-user", "valid-password"
    )
    assert user is not None
    assert user["username"] == "verify-user"


def test_verify_user_password_wrong(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    api_core.create_manager_user(
        db_connection, cache, username="verify-user", password="valid-password"
    )

    assert (
        api_core.verify_user_password(db_connection, "verify-user", "wrong-password")
        is None
    )


def test_verify_user_unknown_returns_none(db_connection: sqlite3.Connection) -> None:
    assert api_core.verify_user_password(
        db_connection, "unknown-user", "valid-password"
    ) is None


def test_login_redirects_to_dashboard(web_client: FlaskClient) -> None:
    response = web_client.post(
        "/login",
        data={"username": "web-test-manager", "password": "test-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_login_wrong_password(web_client: FlaskClient) -> None:
    response = web_client.post(
        "/login",
        data={"username": "web-test-manager", "password": "wrong-password"},
    )

    assert response.status_code == 200
    assert "không đúng" in response.get_data(as_text=True)


def test_dashboard_requires_login(web_client: FlaskClient) -> None:
    response = web_client.get("/dashboard")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_dashboard_status_includes_new_events_and_ocr(
    authenticated_client: FlaskClient, db_connection: sqlite3.Connection
) -> None:
    """The polling response reflects both a new image and a later OCR result."""
    first = authenticated_client.get("/api/dashboard-status")
    assert first.status_code == 200
    assert "Chưa có sự kiện" in first.json["events_html"]

    db_connection.execute(
        """INSERT INTO events (ts, gate, direction, result, reason_code, image_path)
           VALUES ('2026-10-11T13:50:43', 'in', 'in', 'allow', 'ok',
                   'data\\images\\capture.jpg')"""
    )
    db_connection.commit()
    second = authenticated_client.get("/api/dashboard-status")
    assert "Xem ảnh" in second.json["events_html"]
    assert "/images/capture.jpg" in second.json["events_html"]
    assert "18A41083" not in second.json["events_html"]

    db_connection.execute("UPDATE events SET ocr_plate = '18A41083'")
    db_connection.commit()
    third = authenticated_client.get("/api/dashboard-status")
    assert "18A41083" in third.json["events_html"]


def test_logout_clears_session(web_client: FlaskClient) -> None:
    web_client.post(
        "/login",
        data={"username": "web-test-manager", "password": "test-password"},
    )

    response = web_client.post("/logout")

    assert response.status_code == 302
    with web_client.session_transaction() as session:
        assert "user_id" not in session
