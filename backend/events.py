"""Database event recording, kept separate from access decisions."""

import sqlite3
from datetime import datetime

from backend.cache import CacheState


def _resolve_credential(
    connection: sqlite3.Connection, credential_id: int
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, vehicle_id FROM credentials WHERE id = ?", (credential_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Credential not found: {credential_id}")
    return row


def _record_event(
    connection: sqlite3.Connection,
    *,
    gate: str,
    credential_id: int,
    vehicle_id: int,
    result: str,
    reason_code: str,
    now: datetime,
) -> None:
    timestamp = now.isoformat()
    connection.execute(
        """
        INSERT INTO events (
            ts, gate, direction, credential_id, vehicle_id, result,
            reason_code, t_tap
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            gate,
            gate,
            credential_id,
            vehicle_id,
            result,
            reason_code,
            timestamp,
        ),
    )


def confirm_pass(
    gate: str,
    credential_id: int,
    now: datetime,
    connection: sqlite3.Connection,
    cache: CacheState,
) -> None:
    """Update occupancy, record a confirmed passage, and refresh the RAM cache."""
    if gate not in ("in", "out"):
        raise ValueError("gate must be 'in' or 'out'")

    credential = _resolve_credential(connection, credential_id)
    in_lot = 1 if gate == "in" else 0
    with connection:
        connection.execute(
            "UPDATE vehicles SET in_lot = ? WHERE id = ?",
            (in_lot, int(credential["vehicle_id"])),
        )
        _record_event(
            connection,
            gate=gate,
            credential_id=credential_id,
            vehicle_id=int(credential["vehicle_id"]),
            result="allow",
            reason_code="ok",
            now=now,
        )
    cache.refresh_from_db()


def record_no_entry(
    gate: str,
    credential_id: int,
    now: datetime,
    connection: sqlite3.Connection,
) -> None:
    """Record a no-entry timeout without changing vehicle occupancy."""
    if gate not in ("in", "out"):
        raise ValueError("gate must be 'in' or 'out'")

    credential = _resolve_credential(connection, credential_id)
    with connection:
        _record_event(
            connection,
            gate=gate,
            credential_id=credential_id,
            vehicle_id=int(credential["vehicle_id"]),
            result="deny",
            reason_code="no_entry",
            now=now,
        )
