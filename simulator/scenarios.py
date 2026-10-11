"""Automated WebSocket scenarios configured through the HTTP test API."""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from websockets.asyncio.client import connect

from simulator.test_client import TestClient


logger = logging.getLogger(__name__)
Scenario = Callable[["ScenarioContext"], Awaitable[None]]


class ScenarioFailure(AssertionError):
    """Raised when a simulator scenario does not meet its expected result."""


class ScenarioContext:
    """Resources shared by a scenario run."""

    def __init__(
        self,
        *,
        server: str,
        api_key: str,
        connection: sqlite3.Connection,
        test_client: TestClient,
    ) -> None:
        """Store WebSocket, read-only DB, and HTTP test-client resources."""
        self.server = server
        self.api_key = api_key
        self.connection = connection
        self.test_client = test_client

    async def force_server_refresh(self) -> None:
        """Refresh the backend cache through its authenticated HTTP endpoint."""
        await self.test_client.refresh_cache()

    async def tap(self, gate: str, uid: str) -> dict[str, Any]:
        """Connect to a gate, send one tap, and return its decision."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Gate-Id": gate,
        }
        async with connect(
            self.server,
            additional_headers=headers,
            ping_interval=None,
        ) as websocket:
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
            response = await websocket.recv()
        try:
            result: Any = json.loads(response)
        except json.JSONDecodeError as error:
            raise ScenarioFailure(f"Server returned invalid JSON: {response!r}") from error
        if not isinstance(result, dict) or result.get("type") != "decision":
            raise ScenarioFailure(f"Unexpected server response: {result!r}")
        return result

    async def pass_vehicle(self, gate: str, uid: str) -> None:
        """Tap, confirm passage on the same socket, and verify occupancy by read."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Gate-Id": gate,
        }
        async with connect(
            self.server,
            additional_headers=headers,
            ping_interval=None,
        ) as websocket:
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
            response = json.loads(await websocket.recv())
            if not isinstance(response, dict) or response.get("allow") is not True:
                raise ScenarioFailure(f"Expected allow decision, got {response!r}")
            await websocket.send(json.dumps({"type": "pass_confirmed"}))

        expected = gate == "in"
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            row = self.connection.execute(
                """
                SELECT v.in_lot FROM credentials AS c
                JOIN vehicles AS v ON v.id = c.vehicle_id
                WHERE c.value = ?
                """,
                (uid,),
            ).fetchone()
            if row is not None and bool(row["in_lot"]) is expected:
                return
            await asyncio.sleep(0.02)
        raise ScenarioFailure("Pass confirmation did not update in_lot")

    def expect(self, name: str, actual: Any, expected: Any) -> None:
        """Raise an informative failure when an actual value differs."""
        if actual != expected:
            raise ScenarioFailure(f"{name}: expected {expected!r}, got {actual!r}")


async def scenario_valid_in(context: ScenarioContext) -> None:
    """Verify entry after setting occupancy through the test API."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.force_server_refresh()
    await context.pass_vehicle("in", "UID-CAR")


async def scenario_valid_out(context: ScenarioContext) -> None:
    """Verify exit after setting occupancy through the test API."""
    await context.test_client.set_in_lot("CAR-1", True)
    await context.force_server_refresh()
    await context.pass_vehicle("out", "UID-CAR")


async def scenario_expired(context: ScenarioContext) -> None:
    """Verify an expired plan is denied at entry."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.test_client.set_plan_expiry("CAR-1", date(2000, 1, 1))
    await context.force_server_refresh()
    result = await context.tap("in", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "plan_expired")


async def scenario_already_in(context: ScenarioContext) -> None:
    """Verify an occupied vehicle is denied at entry."""
    await context.test_client.set_in_lot("CAR-1", True)
    await context.test_client.set_plan_expiry("CAR-1", date(2099, 12, 31))
    await context.force_server_refresh()
    result = await context.tap("in", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "already_in_lot")


async def scenario_not_in(context: ScenarioContext) -> None:
    """Verify a vehicle outside the lot is denied at exit."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.force_server_refresh()
    result = await context.tap("out", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "not_in_lot")


async def scenario_lot_full(context: ScenarioContext) -> None:
    """Verify entry denial when all total parking spaces are occupied."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.test_client.set_in_lot("MOTO-1", True)
    for index in range(19):
        await context.test_client.set_in_lot(f"SIM-FULL-CAR-{index}", True)
    await context.force_server_refresh()
    result = await context.tap("in", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "lot_full")


async def scenario_unknown(context: ScenarioContext) -> None:
    """Verify an unknown card is denied."""
    result = await context.tap("in", "SIM-UNKNOWN")
    context.expect("reason_code", result.get("reason_code"), "card_unknown")


async def scenario_no_entry(context: ScenarioContext) -> None:
    """Verify no-entry timeout leaves the vehicle outside the lot."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.test_client.set_in_lot("MOTO-1", False)
    for index in range(19):
        await context.test_client.set_in_lot(f"SIM-FULL-CAR-{index}", False)
    await context.force_server_refresh()

    headers = {
        "Authorization": f"Bearer {context.api_key}",
        "X-Gate-Id": "in",
    }
    async with connect(
        context.server,
        additional_headers=headers,
        ping_interval=None,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "tap",
                    "gate": "in",
                    "uid": "UID-CAR",
                    "t_tap": int(time.monotonic() * 1000),
                }
            )
        )
        response = json.loads(await websocket.recv())
        if not isinstance(response, dict) or response.get("allow") is not True:
            raise ScenarioFailure(f"Expected allow decision, got {response!r}")
        await asyncio.sleep(2.1)
        await websocket.send(json.dumps({"type": "no_entry_timeout"}))

    row = context.connection.execute(
        "SELECT in_lot FROM vehicles WHERE plate = 'CAR-1'"
    ).fetchone()
    if row is None:
        raise ScenarioFailure("CAR-1 does not exist in the verification database")
    context.expect("in_lot after timeout", bool(row["in_lot"]), False)


async def scenario_reconnect(context: ScenarioContext) -> None:
    """Verify a fresh connection can make a decision after reconnecting."""
    headers = {
        "Authorization": f"Bearer {context.api_key}",
        "X-Gate-Id": "in",
    }
    async with connect(
        context.server,
        additional_headers=headers,
        ping_interval=None,
    ):
        pass
    result = await context.tap("in", "SIM-UNKNOWN")
    context.expect("reason_code after reconnect", result.get("reason_code"), "card_unknown")


async def scenario_plan_expired_via_clock(context: ScenarioContext) -> None:
    """Verify plan expiry against a fixed future decision clock."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.test_client.set_plan_expiry("CAR-1", date(2025, 1, 1))
    await context.test_client.set_clock(datetime(2030, 1, 1, 12, 0, 0))
    await context.force_server_refresh()
    result = await context.tap("in", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "plan_expired")


async def scenario_plan_valid_via_clock(context: ScenarioContext) -> None:
    """Verify a later expiry remains valid under a fixed decision clock."""
    await context.test_client.set_in_lot("CAR-1", False)
    await context.test_client.set_plan_expiry("CAR-1", date(2030, 1, 1))
    await context.test_client.set_clock(datetime(2026, 10, 6, 12, 0, 0))
    await context.force_server_refresh()
    result = await context.tap("in", "UID-CAR")
    context.expect("reason_code", result.get("reason_code"), "ok")


SCENARIOS: tuple[tuple[str, Scenario], ...] = (
    ("scenario_valid_in", scenario_valid_in),
    ("scenario_valid_out", scenario_valid_out),
    ("scenario_expired", scenario_expired),
    ("scenario_already_in", scenario_already_in),
    ("scenario_not_in", scenario_not_in),
    ("scenario_lot_full", scenario_lot_full),
    ("scenario_unknown", scenario_unknown),
    ("scenario_no_entry", scenario_no_entry),
    ("scenario_reconnect", scenario_reconnect),
    ("scenario_plan_expired_via_clock", scenario_plan_expired_via_clock),
    ("scenario_plan_valid_via_clock", scenario_plan_valid_via_clock),
)


async def run_scenarios(context: ScenarioContext) -> bool:
    """Run scenarios in order and always clear the server clock override."""
    all_passed = True
    for name, scenario in SCENARIOS:
        try:
            await scenario(context)
        except Exception as error:
            all_passed = False
            print(f"[SCENARIO] {name} ... FAIL: {error}")
            logger.exception("Scenario failed: %s", name)
        else:
            print(f"[SCENARIO] {name} ... PASS")
        finally:
            try:
                await context.test_client.reset_clock()
            except Exception:
                all_passed = False
                logger.exception("Failed to reset test clock after %s", name)
    return all_passed
