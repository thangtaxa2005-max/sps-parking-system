"""WebSocket session registry and message delivery helpers."""

import asyncio
import _thread
import json
import logging
import sqlite3
import threading
from collections.abc import Awaitable
from typing import Any

from websockets.asyncio.server import ServerConnection

from backend.cache import CacheState


logger = logging.getLogger(__name__)


class SessionManager:
    """Manage one active WebSocket connection for each gate."""

    def __init__(
        self, connection: sqlite3.Connection, cache: CacheState
    ) -> None:
        """Create a manager bound to the server's database and cache."""
        self.sessions: dict[str, ServerConnection] = {}
        self.connection = connection
        self.cache = cache
        self.db_lock: _thread.RLock = cache.db_lock
        self.last_allow_credential: dict[str, int] = {}
        self.background_tasks: set[asyncio.Task[None]] = set()

    async def register(self, gate_id: str, websocket: ServerConnection) -> None:
        """Register a connection, closing any previous session for that gate."""
        previous = self.sessions.get(gate_id)
        if previous is not None and previous is not websocket:
            await previous.close(code=4003, reason="Replaced by a newer session")
        self.sessions[gate_id] = websocket

    def unregister(
        self, gate_id: str, websocket: ServerConnection | None = None
    ) -> None:
        """Remove a gate session if it still matches the supplied connection."""
        current = self.sessions.get(gate_id)
        if current is not None and (websocket is None or current is websocket):
            del self.sessions[gate_id]

    def get(self, gate_id: str) -> ServerConnection | None:
        """Return the active connection for a gate, if present."""
        return self.sessions.get(gate_id)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Send a JSON object to every active gate connection."""
        await asyncio.gather(
            *(self.send_to(gate_id, msg) for gate_id in tuple(self.sessions))
        )

    async def send_to(self, gate_id: str, msg: dict[str, Any]) -> bool:
        """Send a JSON object to one gate, returning false on delivery failure."""
        websocket = self.get(gate_id)
        if websocket is None:
            logger.warning("No active WebSocket session for gate=%s", gate_id)
            return False
        try:
            await websocket.send(json.dumps(msg, separators=(",", ":")))
        except Exception:
            logger.exception("Failed to send WebSocket message to gate=%s", gate_id)
            return False
        return True

    def spawn_background(self, awaitable: Awaitable[None]) -> None:
        """Track a best-effort background task until it completes."""
        task = asyncio.create_task(awaitable)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
