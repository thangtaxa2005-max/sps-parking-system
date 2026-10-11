"""Async WebSocket server integration tests."""

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.server import serve

from backend.cache import CacheState
from backend import api_core
from backend.db import initialize_schema
from backend.ws_server import make_connection_handler
from backend.ws_session import SessionManager
from tests.conftest import add_vehicle


def _approved_request(connection: sqlite3.Connection) -> int:
    unit_id = connection.execute(
        "SELECT id FROM units WHERE unit_code = 'TEST-UNIT'"
    ).fetchone()["id"]
    cursor = connection.execute(
        """
        INSERT INTO registration_requests
            (unit_id, plate, vehicle_type, plan_type, status, created_at)
        VALUES (?, 'ENROLL-1', 'car', 'year', 'approved_waiting_card', '2026-01-01T00:00:00')
        """,
        (unit_id,),
    )
    connection.commit()
    return int(cursor.lastrowid)


TEST_API_KEY = "test-api-key"


@pytest_asyncio.fixture
async def websocket_environment() -> AsyncIterator[dict[str, object]]:
    """Start an isolated server backed by an in-memory SQLite database."""
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute("INSERT INTO units (unit_code) VALUES ('TEST-UNIT')")
    add_vehicle(connection, plate="CAR-1", vehicle_type="car", uid="UID-CAR")
    add_vehicle(connection, plate="MOTO-1", vehicle_type="moto", uid="UID-MOTO")
    connection.commit()
    cache = CacheState(connection)
    sessions = SessionManager(connection, cache)
    async with serve(
        make_connection_handler(sessions, cache, TEST_API_KEY),
        "127.0.0.1",
        0,
        ping_interval=None,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        yield {
            "uri": f"ws://127.0.0.1:{port}/ws",
            "connection": connection,
            "cache": cache,
            "sessions": sessions,
        }
        tasks = tuple(sessions.background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    connection.close()


def _headers(api_key: str = TEST_API_KEY, gate: str | None = "in") -> dict[str, str]:
    """Build test handshake headers."""
    headers = {"Authorization": f"Bearer {api_key}"}
    if gate is not None:
        headers["X-Gate-Id"] = gate
    return headers


async def _tap(
    websocket: ClientConnection, gate: str, uid: str
) -> dict[str, object]:
    """Send one tap frame and receive its decision."""
    await websocket.send(
        json.dumps(
            {
                "type": "tap",
                "gate": gate,
                "uid": uid,
                "t_tap": int(time.monotonic() * 1000),
            }
        )
    )
    result = json.loads(await websocket.recv())
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_reject_wrong_api_key(websocket_environment: dict[str, object]) -> None:
    """Reject a client with an invalid API key using close code 4001."""
    with pytest.raises(ConnectionClosed) as exc_info:
        async with connect(
            websocket_environment["uri"], additional_headers=_headers("wrong")
        ) as websocket:
            await websocket.recv()
    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4001


@pytest.mark.asyncio
async def test_reject_missing_gate_id(
    websocket_environment: dict[str, object],
) -> None:
    """Reject a client without X-Gate-Id using close code 4002."""
    with pytest.raises(ConnectionClosed) as exc_info:
        async with connect(
            websocket_environment["uri"], additional_headers=_headers(gate=None)
        ) as websocket:
            await websocket.recv()
    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4002


@pytest.mark.asyncio
async def test_tap_valid_returns_decision(
    websocket_environment: dict[str, object],
) -> None:
    """Return an allow decision for the seeded valid RFID card."""
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "UID-CAR")
    assert response["type"] == "decision"
    assert response["allow"] is True
    assert response["reason_code"] == "ok"


@pytest.mark.asyncio
async def test_tap_unknown_returns_deny(
    websocket_environment: dict[str, object],
) -> None:
    """Return a card_unknown denial for an unregistered UID."""
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "NOT-REGISTERED")
    assert response["allow"] is False
    assert response["reason_code"] == "card_unknown"


@pytest.mark.asyncio
async def test_tap_during_enroll_creates_credential(
    websocket_environment: dict[str, object],
) -> None:
    """Enrollment attaches the card but explicitly does not open the gate."""
    connection = websocket_environment["connection"]
    cache = websocket_environment["cache"]
    request_id = _approved_request(connection)
    api_core.set_enroll_state(
        connection, cache, gate="in", request_id=request_id
    )
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "UID-ENROLL")
    assert response["allow"] is False
    assert response["reason_code"] == "enrolled"
    assert connection.execute(
        "SELECT 1 FROM credentials WHERE value = 'UID-ENROLL'"
    ).fetchone() is not None
    assert connection.execute(
        "SELECT status FROM registration_requests WHERE id = ?", (request_id,)
    ).fetchone()["status"] == "active"
    assert api_core.get_enroll_state(connection) is None


@pytest.mark.asyncio
async def test_tap_wrong_gate_during_enroll_ignores_enroll(
    websocket_environment: dict[str, object],
) -> None:
    """A tap on the other gate leaves enrollment active."""
    connection = websocket_environment["connection"]
    cache = websocket_environment["cache"]
    api_core.set_enroll_state(connection, cache, gate="in", vehicle_id=1)
    async with connect(
        websocket_environment["uri"], additional_headers=_headers(gate="out")
    ) as websocket:
        response = await _tap(websocket, "out", "UID-NEW-GATE")
    assert response["reason_code"] == "card_unknown"
    assert api_core.get_enroll_state(connection)["gate"] == "in"
    assert connection.execute(
        "SELECT 1 FROM credentials WHERE value = 'UID-NEW-GATE'"
    ).fetchone() is None


@pytest.mark.asyncio
async def test_tap_when_uid_exists_during_enroll_ignores(
    websocket_environment: dict[str, object],
) -> None:
    """An existing UID follows normal decision logic and does not consume enroll."""
    connection = websocket_environment["connection"]
    cache = websocket_environment["cache"]
    api_core.set_enroll_state(connection, cache, gate="in", vehicle_id=1)
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "UID-CAR")
    assert response["reason_code"] == "ok"
    assert api_core.get_enroll_state(connection)["gate"] == "in"


@pytest.mark.asyncio
async def test_decision_latency_under_20ms(
    websocket_environment: dict[str, object],
) -> None:
    """Keep the median of ten tap-to-decision round trips below 20 ms."""
    timings: list[float] = []
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        for _ in range(10):
            started = time.monotonic()
            response = await _tap(websocket, "in", "UID-CAR")
            timings.append((time.monotonic() - started) * 1000)
            assert response["allow"] is True
    timings.sort()
    median = (timings[4] + timings[5]) / 2
    assert median < 20, f"median latency was {median:.2f} ms"


@pytest.mark.asyncio
async def test_pass_confirmed_updates_in_lot(
    websocket_environment: dict[str, object],
) -> None:
    """Update the vehicle's occupancy after pass_confirmed."""
    connection = websocket_environment["connection"]
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "UID-CAR")
        assert response["allow"] is True
        assert response["free_total"] == 20
        await websocket.send(json.dumps({"type": "pass_confirmed"}))
        async with asyncio.timeout(2):
            while True:
                row = connection.execute(
                    "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
                ).fetchone()
                if row["in_lot"] == 1:
                    break
                await asyncio.sleep(0.01)
    assert websocket_environment["cache"].free_total == 19


@pytest.mark.asyncio
async def test_no_entry_timeout_keeps_vehicle_out(
    websocket_environment: dict[str, object],
) -> None:
    """Record no-entry timeout without changing the vehicle's occupancy."""
    connection = websocket_environment["connection"]
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        response = await _tap(websocket, "in", "UID-CAR")
        assert response["allow"] is True
        await websocket.send(json.dumps({"type": "no_entry_timeout"}))
        async with asyncio.timeout(2):
            while True:
                row = connection.execute(
                    "SELECT reason_code FROM events WHERE reason_code = 'no_entry'"
                ).fetchone()
                if row is not None:
                    break
                await asyncio.sleep(0.01)
    occupancy = connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()
    assert occupancy["in_lot"] == 0


@pytest.mark.asyncio
async def test_stuck_open_notifies_managers(
    websocket_environment: dict[str, object],
) -> None:
    """Create an alert notification for each manager on stuck_open."""
    connection = websocket_environment["connection"]
    connection.execute("INSERT INTO users (role) VALUES ('manager')")
    connection.commit()
    async with connect(
        websocket_environment["uri"], additional_headers=_headers()
    ) as websocket:
        await websocket.send(
            json.dumps({"type": "stuck_open", "duration_s": 31})
        )
        async with asyncio.timeout(2):
            while True:
                notification = connection.execute(
                    "SELECT level, content FROM notifications"
                ).fetchone()
                if notification is not None:
                    break
                await asyncio.sleep(0.01)
    assert notification["level"] == "alert"
    assert notification["content"] == "Cổng in mở quá 31 giây"


@pytest.mark.asyncio
async def test_one_gate_one_session(
    websocket_environment: dict[str, object],
) -> None:
    """Close the older socket when a second client claims the same gate."""
    old_socket = await connect(
        websocket_environment["uri"], additional_headers=_headers()
    )
    new_socket = await connect(
        websocket_environment["uri"], additional_headers=_headers()
    )
    try:
        with pytest.raises(ConnectionClosed) as exc_info:
            await old_socket.recv()
        assert exc_info.value.rcvd is not None
        assert exc_info.value.rcvd.code == 4003
        response = await _tap(new_socket, "in", "UID-CAR")
        assert response["allow"] is True
    finally:
        await old_socket.close()
        await new_socket.close()
