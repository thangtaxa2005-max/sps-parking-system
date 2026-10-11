"""SQLite connection and schema helpers."""

import sqlite3
from pathlib import Path

from backend.config import (
    ALERT_RETENTION_DAYS,
    CAPACITY_TOTAL,
    DB_PATH,
    DEFAULT_QUOTA_CAR,
    DEFAULT_QUOTA_MOTO,
    IMAGE_RETENTION_DAYS,
    OCR_THRESHOLD,
)
from backend.models import SCHEMA_SQL


DEFAULT_SETTINGS = {
    "capacity_total": str(CAPACITY_TOTAL),
    "default_quota_car": str(DEFAULT_QUOTA_CAR),
    "default_quota_moto": str(DEFAULT_QUOTA_MOTO),
    "ocr_threshold": str(OCR_THRESHOLD),
    "image_retention_days": str(IMAGE_RETENTION_DAYS),
    "alert_retention_days": str(ALERT_RETENTION_DAYS),
}


def connect_database(path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with row access and foreign keys enabled."""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply schema migrations that CREATE TABLE IF NOT EXISTS cannot handle."""
    user_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(users)")
    }
    if "username" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "password_hash" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username "
        "ON users(username)"
    )
    notification_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(notifications)")
    }
    if "resolved_at" not in notification_columns:
        connection.execute("ALTER TABLE notifications ADD COLUMN resolved_at TEXT")
    if "resolved_by_user_id" not in notification_columns:
        connection.execute(
            "ALTER TABLE notifications ADD COLUMN resolved_by_user_id INTEGER REFERENCES users(id)"
        )
    if "event_id" not in notification_columns:
        connection.execute(
            "ALTER TABLE notifications ADD COLUMN event_id INTEGER REFERENCES events(id)"
        )
    connection.commit()


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the schema and insert missing default settings."""
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_SQL)
    _apply_migrations(connection)
    connection.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        DEFAULT_SETTINGS.items(),
    )
    connection.commit()


def query_one(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> sqlite3.Row | None:
    """Return the first row matching a parameterized query, if any."""
    return connection.execute(sql, parameters).fetchone()


def query_all(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> list[sqlite3.Row]:
    """Return all rows matching a parameterized query."""
    return connection.execute(sql, parameters).fetchall()


def get_setting(connection: sqlite3.Connection, key: str) -> str:
    """Return a setting value or raise KeyError when it is not configured."""
    row = query_one(connection, "SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        raise KeyError(f"Setting not found: {key}")
    return str(row["value"])
