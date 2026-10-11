"""Async WebSocket server used by ESP32 gates."""

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from backend.cache import CacheState
from backend.camera import CameraManager
from backend.config import (
    CAMERA_URL_IN,
    CAMERA_URL_OUT,
    DB_PATH,
    OCR_CONF_CHAR,
    OCR_CONF_PLATE,
    OCR_MODEL_CHAR_PATH,
    OCR_MODEL_PLATE_PATH,
)
from backend.db import connect_database, initialize_schema
from backend.handlers import dispatch_message
from backend import api_core
from backend.cleanup import cleanup_images
from backend.ocr import OcrEngine
from backend.test_server import start_test_server
from backend.ws_session import SessionManager


logger = logging.getLogger(__name__)
GATE_PATH = "/ws"


def configure_logging() -> None:
    """Configure timestamped stdout logging with the requested WARN label."""
    logging.addLevelName(logging.WARNING, "WARN")
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_api_key() -> str:
    """Require the shared gate API key to be configured explicitly."""
    api_key = os.environ.get("SPS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("SPS_API_KEY is required")
    return api_key


def make_connection_handler(
    sessions: SessionManager,
    cache: CacheState,
    api_key: str,
    camera: CameraManager | None = None,
    ocr_engine: OcrEngine | None = None,
) -> Callable[[ServerConnection], Awaitable[None]]:
    """Build a server connection handler bound to shared runtime state."""
    if camera is None:
        camera = CameraManager("", "")

    async def connection_handler(websocket: ServerConnection) -> None:
        request = websocket.request
        path = request.path if request is not None else ""
        headers = request.headers if request is not None else {}
        if path.split("?", maxsplit=1)[0] != GATE_PATH:
            logger.warning("Rejecting connection with invalid path=%s", path)
            await websocket.close(code=4002, reason="Invalid WebSocket endpoint")
            return

        authorization = headers.get("Authorization", "")
        if authorization != f"Bearer {api_key}":
            logger.warning("Rejecting connection with invalid API key")
            await websocket.close(code=4001, reason="Invalid API key")
            return

        gate_id = headers.get("X-Gate-Id")
        if gate_id not in ("in", "out"):
            logger.warning("Rejecting connection with invalid gate id=%r", gate_id)
            await websocket.close(code=4002, reason="Invalid gate id")
            return

        await sessions.register(gate_id, websocket)
        logger.info("WebSocket connected gate=%s", gate_id)
        try:
            async for raw_message in websocket:
                try:
                    payload: Any = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Ignoring invalid JSON from gate=%s", gate_id)
                    continue
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("type"), str
                ):
                    logger.warning("Ignoring malformed message from gate=%s", gate_id)
                    continue
                if sessions.get(gate_id) is not websocket:
                    break
                await dispatch_message(
                    sessions, gate_id, payload, cache, camera, ocr_engine
                )
        except Exception:
            logger.exception("WebSocket handler failed for gate=%s", gate_id)
        finally:
            sessions.unregister(gate_id, websocket)
            logger.info("WebSocket disconnected gate=%s", gate_id)

    return connection_handler


async def _ping_sessions(sessions: SessionManager) -> None:
    """Send application-level pings every 25 seconds."""
    while True:
        await asyncio.sleep(25)
        await sessions.broadcast({"type": "ping"})


async def _refresh_cache(cache: CacheState) -> None:
    """Refresh shared cache state from SQLite every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(cache.refresh_from_db)
        except Exception:
            logger.exception("Periodic cache refresh failed")


async def _dispatch_gate_commands(sessions: SessionManager) -> None:
    """Poll SQLite for manual commands created by the Flask process."""
    while True:
        try:
            commands = await asyncio.to_thread(api_core.list_pending_gate_commands, sessions.connection)
            for command in commands:
                delivered = await sessions.send_to(
                    str(command["gate"]),
                    {"type": str(command["command"]), "command_id": int(command["id"])},
                )
                if delivered:
                    await asyncio.to_thread(
                        api_core.mark_gate_command_sent, sessions.connection, int(command["id"])
                    )
        except Exception:
            logger.exception("Gate command polling failed")
        await asyncio.sleep(0.2)


async def _cleanup_worker(connection) -> None:
    """Run image retention cleanup once per hour."""
    while True:
        try:
            await asyncio.to_thread(cleanup_images, connection)
        except Exception:
            logger.exception("Image retention cleanup failed")
        await asyncio.sleep(3600)


async def main() -> None:
    """Start the WebSocket server and its shared cache maintenance tasks."""
    configure_logging()
    connection = connect_database(DB_PATH)
    initialize_schema(connection)
    cache = CacheState(connection)
    sessions = SessionManager(connection, cache)
    camera = CameraManager(CAMERA_URL_IN, CAMERA_URL_OUT)
    camera.start()
    ocr_engine: OcrEngine | None = None
    try:
        ocr_engine = OcrEngine(
            OCR_MODEL_PLATE_PATH,
            OCR_MODEL_CHAR_PATH,
            OCR_CONF_PLATE,
            OCR_CONF_CHAR,
        )
    except Exception:
        logger.exception("Failed to load OCR engine; OCR is disabled")
    api_key = get_api_key()
    ping_task = asyncio.create_task(_ping_sessions(sessions))
    refresh_task = asyncio.create_task(_refresh_cache(cache))
    command_task = asyncio.create_task(_dispatch_gate_commands(sessions))
    cleanup_task = asyncio.create_task(_cleanup_worker(connection))
    runner = None
    try:
        runner = await start_test_server(connection, cache, api_key)
        async with serve(
            make_connection_handler(sessions, cache, api_key, camera, ocr_engine),
            "0.0.0.0",
            8765,
            ping_interval=None,
        ) as server:
            logger.info("WebSocket server listening on 0.0.0.0:8765/ws")
            await server.serve_forever()
    finally:
        ping_task.cancel()
        refresh_task.cancel()
        command_task.cancel()
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await ping_task
        with suppress(asyncio.CancelledError):
            await refresh_task
        with suppress(asyncio.CancelledError):
            await command_task
        with suppress(asyncio.CancelledError):
            await cleanup_task
        if sessions.background_tasks:
            await asyncio.gather(*sessions.background_tasks, return_exceptions=True)
        if runner is not None:
            await runner.cleanup()
        camera.stop()
        connection.close()


if __name__ == "__main__":
    asyncio.run(main())
