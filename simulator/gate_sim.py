"""Command-line WebSocket client that simulates an ESP32 gate."""

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientConnectorError
from websockets.asyncio.client import ClientConnection, connect

from backend.config import DB_PATH
from simulator.scenarios import ScenarioContext, run_scenarios
from simulator.test_client import TestClient


logger = logging.getLogger(__name__)
DEFAULT_SERVER = "ws://localhost:8765/ws"


def _parse_args() -> argparse.Namespace:
    """Parse gate simulator command-line arguments."""
    parser = argparse.ArgumentParser(description="Simulate an SPS ESP32 gate")
    parser.add_argument("--gate", choices=("in", "out"), required=True)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("SPS_API_KEY", ""),
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--auto", action="store_true")
    modes.add_argument("--uid")
    modes.add_argument("--interactive", action="store_true")
    return parser.parse_args()


def _headers(gate: str, api_key: str) -> dict[str, str]:
    """Build authentication and gate-identification headers."""
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Gate-Id": gate,
    }


async def _connect_forever(
    server: str, gate: str, api_key: str
) -> ClientConnection:
    """Connect to the WebSocket server, retrying every three seconds."""
    while True:
        try:
            return await connect(
                server,
                additional_headers=_headers(gate, api_key),
                ping_interval=None,
            )
        except Exception as error:
            print(f"[SIM] Reconnecting... ({error})", flush=True)
            await asyncio.sleep(3)


async def _receive_messages(websocket: ClientConnection) -> None:
    """Print server messages and automatically confirm allowed passages."""
    async for raw_message in websocket:
        try:
            response: Any = json.loads(raw_message)
        except json.JSONDecodeError:
            print(f"[SIM] Invalid server JSON: {raw_message}", flush=True)
            continue
        if not isinstance(response, dict):
            continue
        if response.get("type") == "decision":
            print(
                "[SIM] Decision: allow=%s reason=%s oled=%s free_total=%s"
                % (
                    response.get("allow"),
                    response.get("reason_code"),
                    response.get("oled_message"),
                    response.get("free_total"),
                ),
                flush=True,
            )
            if response.get("allow") is True:
                await asyncio.sleep(1.5)
                await websocket.send(json.dumps({"type": "pass_confirmed"}))
        else:
            print(f"[SIM] Server message: {response}", flush=True)


async def _send(
    websocket: ClientConnection, gate: str, message_type: str, **fields: Any
) -> None:
    """Send one protocol message to the server."""
    message = {"type": message_type, "gate": gate, **fields}
    await websocket.send(json.dumps(message, separators=(",", ":")))


async def _interactive(websocket: ClientConnection, gate: str) -> None:
    """Read interactive commands and send their matching protocol messages."""
    receiver = asyncio.create_task(_receive_messages(websocket))
    try:
        while True:
            command = await asyncio.to_thread(input, "gate> ")
            parts = command.strip().split()
            if not parts:
                continue
            name, *arguments = parts
            if name == "quit":
                return
            if name == "tap" and len(arguments) == 1:
                await _send(
                    websocket,
                    gate,
                    "tap",
                    uid=arguments[0],
                    t_tap=int(time.monotonic() * 1000),
                )
            elif name == "pass" and not arguments:
                await _send(websocket, gate, "pass_confirmed")
            elif name == "timeout" and not arguments:
                await _send(websocket, gate, "no_entry_timeout")
            elif name == "obst" and not arguments:
                await _send(websocket, gate, "obstruction")
            elif name == "stuck" and len(arguments) == 1:
                await _send(
                    websocket,
                    gate,
                    "stuck_open",
                    duration_s=float(arguments[0]),
                )
            else:
                print("Commands: tap <uid>, pass, timeout, obst, stuck <secs>, quit")
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)


async def _one_tap(
    websocket: ClientConnection, gate: str, uid: str
) -> None:
    """Send one tap and display the decision response."""
    await _send(websocket, gate, "tap", uid=uid, t_tap=int(time.monotonic() * 1000))
    async for raw_message in websocket:
        try:
            response: Any = json.loads(raw_message)
        except json.JSONDecodeError:
            print(f"[SIM] Invalid server JSON: {raw_message}", flush=True)
            continue
        if not isinstance(response, dict):
            continue
        if response.get("type") != "decision":
            print(f"[SIM] Server message: {response}", flush=True)
            continue
        print(
            "[SIM] Decision: allow=%s reason=%s oled=%s free_total=%s"
            % (
                response.get("allow"),
                response.get("reason_code"),
                response.get("oled_message"),
                response.get("free_total"),
            ),
            flush=True,
        )
        if response.get("allow") is True:
            await asyncio.sleep(1.5)
            await _send(websocket, gate, "pass_confirmed")
        return
    raise ConnectionError("WebSocket closed before a decision was received")


def _open_readonly_database(path: Path) -> sqlite3.Connection:
    """Open the configured SQLite database in enforced read-only mode."""
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


async def _run_auto(server: str, api_key: str) -> int:
    """Check and seed the HTTP test server, then run the scenarios."""
    async with TestClient(api_key=api_key) as test_client:
        try:
            await test_client.ping()
        except ClientConnectorError:
            print(
                "Test server chưa bật. Chạy server với SPS_TEST_MODE=1",
                file=sys.stderr,
            )
            return 2
        await test_client.seed()

        connection = _open_readonly_database(DB_PATH)
        try:
            context = ScenarioContext(
                server=server,
                api_key=api_key,
                connection=connection,
                test_client=test_client,
            )
            return 0 if await run_scenarios(context) else 1
        finally:
            connection.close()


async def _run() -> int:
    """Run the selected simulator mode."""
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if args.auto:
        return await _run_auto(args.server, args.api_key)

    while True:
        websocket = await _connect_forever(args.server, args.gate, args.api_key)
        try:
            if args.interactive:
                await _interactive(websocket, args.gate)
                return 0
            if args.uid:
                await _one_tap(websocket, args.gate, args.uid)
                return 0
        except Exception as error:
            logger.warning("WebSocket disconnected: %s", error)
            print("[SIM] Reconnecting...", flush=True)
            await asyncio.sleep(3)
        finally:
            await websocket.close()


def main() -> None:
    """Run the simulator command-line application."""
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
