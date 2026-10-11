"""Read-through in-memory state used by the decision function."""

import _thread
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

from backend.db import get_setting


class CredentialState(NamedTuple):
    """Cached credential and associated vehicle decision fields."""

    vehicle_id: int
    status: str
    plan_expires_at: date | None
    vehicle_type: str
    in_lot: bool


@dataclass(slots=True)
class CacheState:
    """RAM snapshot of credentials, vehicle occupancy, and parking capacity."""

    connection: sqlite3.Connection = field(repr=False)
    db_lock: _thread.RLock = field(default_factory=threading.RLock, repr=False)
    credentials: dict[str, CredentialState] = field(default_factory=dict)
    credential_ids: dict[str, int] = field(default_factory=dict)
    vehicles: dict[int, bool] = field(default_factory=dict)
    users_by_username: dict[str, sqlite3.Row] = field(default_factory=dict)
    capacity_total: int = 0
    free_total: int = 0

    def __post_init__(self) -> None:
        """Load the initial RAM snapshot when the cache is constructed."""
        self.refresh_from_db()

    def refresh_from_db(self) -> None:
        """Reload credentials, occupancy, capacity, and free-slot counts."""
        with self.db_lock:
            credential_rows = self.connection.execute(
                """
                SELECT c.id, c.value, c.vehicle_id, c.status,
                       v.plan_expires_at, v.vehicle_type, v.in_lot
                FROM credentials AS c
                JOIN vehicles AS v ON v.id = c.vehicle_id
                """
            ).fetchall()
            vehicle_rows = self.connection.execute(
                "SELECT id, in_lot FROM vehicles"
            ).fetchall()
            user_rows = self.connection.execute(
                "SELECT id, username, role, unit_id FROM users "
                "WHERE username IS NOT NULL"
            ).fetchall()

            self.capacity_total = int(
                get_setting(self.connection, "capacity_total")
            )
            self.credentials = {
                str(row["value"]): CredentialState(
                    vehicle_id=int(row["vehicle_id"]),
                    status=str(row["status"]),
                    plan_expires_at=(
                        date.fromisoformat(str(row["plan_expires_at"]))
                        if row["plan_expires_at"] is not None
                        else None
                    ),
                    vehicle_type=str(row["vehicle_type"]),
                    in_lot=bool(row["in_lot"]),
                )
                for row in credential_rows
            }
            self.credential_ids = {
                str(row["value"]): int(row["id"]) for row in credential_rows
            }
            self.vehicles = {
                int(row["id"]): bool(row["in_lot"]) for row in vehicle_rows
            }
            self.users_by_username = {
                str(row["username"]): row for row in user_rows
            }

            occupied_total = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM vehicles WHERE in_lot = 1"
                ).fetchone()[0]
            )
            self.free_total = self.capacity_total - occupied_total

    def get_slots(self) -> int:
        """Return the total number of free parking slots."""
        return self.free_total
