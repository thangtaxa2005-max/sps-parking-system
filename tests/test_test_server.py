"""HTTP test-server endpoint tests."""

import os
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from backend import handlers
from backend.cache import CacheState
from backend.db import initialize_schema
from backend.test_server import _make_app
from tests.conftest import add_vehicle


TEST_API_KEY = "test-api-key"


@pytest_asyncio.fixture
async def test_server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, object]]:
    """Run the test API against an isolated in-memory database."""
    monkeypatch.setenv("SPS_TEST_MODE", "1")

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute("INSERT INTO units (unit_code) VALUES ('TEST-UNIT')")
    add_vehicle(connection, plate="CAR-1", vehicle_type="car", uid="UID-CAR")
    add_vehicle(connection, plate="MOTO-1", vehicle_type="moto", uid="UID-MOTO")
    connection.commit()
    cache = CacheState(connection)

    server = TestServer(_make_app(connection, cache, TEST_API_KEY))
    client = TestClient(server)
    await client.start_server()
    try:
        yield {"client": client, "connection": connection, "cache": cache}
    finally:
        await client.close()
        handlers.set_clock_override(None)
        connection.close()


def _headers(api_key: str = TEST_API_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def _post(
    test_server: dict[str, object],
    path: str,
    payload: object | None = None,
    *,
    api_key: str = TEST_API_KEY,
):
    client = test_server["client"]
    assert isinstance(client, TestClient)
    return await client.post(path, json=payload, headers=_headers(api_key))


@pytest.mark.asyncio
async def test_ping_with_valid_key(test_server: dict[str, object]) -> None:
    response = await _post(test_server, "/test/ping", {})

    assert response.status == 200
    assert (await response.json())["status"] == "ok"


@pytest.mark.asyncio
async def test_ping_without_auth(test_server: dict[str, object]) -> None:
    client = test_server["client"]
    assert isinstance(client, TestClient)
    response = await client.post("/test/ping", json={})

    assert response.status == 401


@pytest.mark.asyncio
async def test_ping_with_wrong_key(test_server: dict[str, object]) -> None:
    response = await _post(test_server, "/test/ping", {}, api_key="wrong-key")

    assert response.status == 401


@pytest.mark.asyncio
async def test_lock_credential(test_server: dict[str, object]) -> None:
    response = await _post(test_server, "/test/lock", {"uid": "UID-CAR"})
    connection = test_server["connection"]
    cache = test_server["cache"]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(cache, CacheState)

    assert response.status == 200
    assert connection.execute(
        "SELECT status FROM credentials WHERE value = 'UID-CAR'"
    ).fetchone()["status"] == "locked"
    assert cache.credentials["UID-CAR"].status == "locked"


@pytest.mark.asyncio
async def test_unlock_after_lock(test_server: dict[str, object]) -> None:
    assert (await _post(test_server, "/test/lock", {"uid": "UID-CAR"})).status == 200
    response = await _post(test_server, "/test/unlock", {"uid": "UID-CAR"})

    assert response.status == 200
    assert test_server["cache"].credentials["UID-CAR"].status == "active"


@pytest.mark.asyncio
async def test_mark_lost_then_unlock_fails(test_server: dict[str, object]) -> None:
    assert (await _post(test_server, "/test/mark_lost", {"uid": "UID-CAR"})).status == 200
    response = await _post(test_server, "/test/unlock", {"uid": "UID-CAR"})

    assert response.status == 400


@pytest.mark.asyncio
async def test_set_in_lot(test_server: dict[str, object]) -> None:
    cache = test_server["cache"]
    connection = test_server["connection"]
    assert isinstance(cache, CacheState)
    assert isinstance(connection, sqlite3.Connection)
    free_before = cache.free_total

    response = await _post(
        test_server, "/test/set_in_lot", {"plate": "CAR-1", "value": True}
    )

    assert response.status == 200
    assert connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()["in_lot"] == 1
    assert cache.free_total == free_before - 1


@pytest.mark.asyncio
async def test_set_plan_expiry(test_server: dict[str, object]) -> None:
    response = await _post(
        test_server,
        "/test/set_plan_expiry",
        {"plate": "CAR-1", "date": "2020-01-01"},
    )
    connection = test_server["connection"]
    assert isinstance(connection, sqlite3.Connection)

    assert response.status == 200
    assert connection.execute(
        "SELECT plan_expires_at FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()["plan_expires_at"] == "2020-01-01"


@pytest.mark.asyncio
async def test_set_clock_and_reset(test_server: dict[str, object]) -> None:
    expected = datetime(2030, 1, 1, 12, 34, 56)
    response = await _post(
        test_server, "/test/set_clock", {"datetime": expected.isoformat()}
    )

    assert response.status == 200
    assert handlers.get_now() == expected

    before_reset = datetime.now()
    response = await _post(test_server, "/test/reset_clock", {})
    after_reset = datetime.now()

    assert response.status == 200
    assert before_reset <= handlers.get_now() <= after_reset


@pytest.mark.asyncio
async def test_unknown_endpoint(test_server: dict[str, object]) -> None:
    response = await _post(test_server, "/test/khong_ton_tai", {})

    assert response.status == 404


@pytest.mark.asyncio
async def test_missing_body_field(test_server: dict[str, object]) -> None:
    response = await _post(test_server, "/test/lock", {})

    assert response.status == 400
