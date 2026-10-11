"""Synchronous business operations shared by server, web, and bot clients."""

import json
import logging
import secrets
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta

import bcrypt

from backend.cache import CacheState
from backend.config import (
    CAPACITY_TOTAL,
    DEFAULT_QUOTA_CAR,
    DEFAULT_QUOTA_MOTO,
    ENROLL_DURATION_S,
)


logger = logging.getLogger(__name__)


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Return editable system settings as strings for the management UI."""
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN "
        "('capacity_total', 'default_quota_car', 'default_quota_moto', 'ocr_threshold')"
    ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def update_settings(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    capacity_total: int,
    default_quota_car: int,
    default_quota_moto: int,
    ocr_threshold: float,
) -> None:
    """Validate and persist manager-editable settings, then refresh cache."""
    if not 1 <= capacity_total <= 10000:
        raise ValueError("Sức chứa phải từ 1 đến 10000.")
    if not 0 <= default_quota_car <= capacity_total:
        raise ValueError("Hạn mức ô tô không hợp lệ.")
    if not 0 <= default_quota_moto <= capacity_total:
        raise ValueError("Hạn mức xe máy không hợp lệ.")
    if not 0.0 <= ocr_threshold <= 1.0:
        raise ValueError("Ngưỡng OCR phải từ 0 đến 1.")
    values = {
        "capacity_total": str(capacity_total),
        "default_quota_car": str(default_quota_car),
        "default_quota_moto": str(default_quota_moto),
        "ocr_threshold": str(ocr_threshold),
    }
    with cache.db_lock, conn:
        conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            values.items(),
        )
        cache.refresh_from_db()


def enqueue_open_command(conn: sqlite3.Connection, *, gate: str, actor: str) -> int:
    """Queue a manual gate-open command for the WebSocket process."""
    if gate not in ("in", "out"):
        raise ValueError("Cổng phải là 'in' hoặc 'out'.")
    actor = actor.strip()
    if not actor:
        raise ValueError("Thiếu người thao tác.")
    with conn:
        cursor = conn.execute(
            "INSERT INTO gate_commands (gate, command, actor, created_at) VALUES (?, 'open_command', ?, ?)",
            (gate, actor, datetime.now().isoformat()),
        )
    return int(cursor.lastrowid)


def list_pending_gate_commands(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return queued commands that haven't been delivered to a gate."""
    return conn.execute(
        "SELECT id, gate, command FROM gate_commands WHERE sent_at IS NULL ORDER BY id"
    ).fetchall()


def mark_gate_command_sent(conn: sqlite3.Connection, command_id: int) -> None:
    """Mark a successfully delivered gate command."""
    with conn:
        conn.execute(
            "UPDATE gate_commands SET sent_at = ? WHERE id = ? AND sent_at IS NULL",
            (datetime.now().isoformat(), command_id),
        )


def get_enroll_state(conn: sqlite3.Connection) -> dict | None:
    """Read settings.enroll_mode and remove malformed or expired state."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'enroll_mode'").fetchone()
    if row is None:
        return None
    try:
        state = json.loads(str(row["value"]))
        if not isinstance(state, dict):
            raise ValueError
        expires_at = datetime.fromisoformat(str(state["expires_at"]))
        if expires_at <= datetime.now():
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        with conn:
            conn.execute("DELETE FROM settings WHERE key = 'enroll_mode'")
        return None
    return state


def set_enroll_state(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    gate: str,
    duration_s: int = ENROLL_DURATION_S,
    request_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """Enable enrollment for one gate and one approved request or vehicle."""
    if gate not in ("in", "out"):
        raise ValueError("Cổng đăng ký phải là 'in' hoặc 'out'.")
    if (
        isinstance(duration_s, bool)
        or not isinstance(duration_s, int)
        or not 10 <= duration_s <= 300
    ):
        raise ValueError("Thời gian đăng ký phải trong khoảng 10 đến 300 giây.")
    if (request_id is None) == (vehicle_id is None):
        raise ValueError("Phải chọn đúng một yêu cầu đăng ký hoặc xe.")

    with cache.db_lock:
        if request_id is not None:
            row = conn.execute(
                "SELECT status FROM registration_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Không tìm thấy yêu cầu đăng ký số {request_id}.")
            if row["status"] != "approved_waiting_card":
                raise ValueError("Yêu cầu đăng ký chưa được duyệt để cấp thẻ.")
        elif conn.execute(
            "SELECT 1 FROM vehicles WHERE id = ?", (vehicle_id,)
        ).fetchone() is None:
            raise ValueError(f"Không tìm thấy xe id={vehicle_id}.")

        started_at = datetime.now()
        state = {
            "gate": gate,
            "request_id": request_id,
            "vehicle_id": vehicle_id,
            "expires_at": (started_at + timedelta(seconds=duration_s)).isoformat(),
            "started_at": started_at.isoformat(),
        }
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('enroll_mode', ?)",
                (json.dumps(state, separators=(",", ":")),),
            )
    return state


def clear_enroll_state(conn: sqlite3.Connection, cache: CacheState) -> None:
    """Delete enrollment state if present."""
    with cache.db_lock, conn:
        conn.execute("DELETE FROM settings WHERE key = 'enroll_mode'")


def attach_credential_from_enroll(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    uid: str,
    gate: str,
) -> dict | None:
    """Attach a new RFID UID to the active enrollment target."""
    with cache.db_lock:
        state = get_enroll_state(conn)
        if state is None or state.get("gate") != gate:
            return None
        if conn.execute(
            "SELECT 1 FROM credentials WHERE value = ?", (uid,)
        ).fetchone() is not None:
            raise ValueError("UID thẻ đã tồn tại.")

        request_id = state.get("request_id")
        vehicle_id = state.get("vehicle_id")
        created_vehicle = False
        with conn:
            if request_id is not None:
                request = conn.execute(
                    "SELECT * FROM registration_requests WHERE id = ?", (request_id,)
                ).fetchone()
                if request is None or request["status"] != "approved_waiting_card":
                    raise ValueError("Yêu cầu đăng ký không còn hợp lệ để cấp thẻ.")
                existing = conn.execute(
                    "SELECT id FROM vehicles WHERE plate = ?", (request["plate"],)
                ).fetchone()
                if existing is None:
                    months = 1 if request["plan_type"] == "month" else 12
                    expiry = add_months(date.today(), months)
                    cursor = conn.execute(
                        """
                        INSERT INTO vehicles (
                            plate, vehicle_type, unit_id, subject_type, plan_expires_at
                        ) VALUES (?, ?, ?, 'resident', ?)
                        """,
                        (
                            request["plate"], request["vehicle_type"],
                            request["unit_id"], expiry.isoformat(),
                        ),
                    )
                    vehicle_id = int(cursor.lastrowid)
                    created_vehicle = True
                else:
                    vehicle_id = int(existing["id"])
                conn.execute(
                    "UPDATE registration_requests SET status = 'active' WHERE id = ?",
                    (request_id,),
                )

            vehicle = conn.execute(
                "SELECT id, plate FROM vehicles WHERE id = ?", (vehicle_id,)
            ).fetchone()
            if vehicle is None:
                raise ValueError(f"Không tìm thấy xe id={vehicle_id}.")
            if request_id is None:
                conn.execute(
                    """
                    UPDATE credentials SET status = 'lost'
                    WHERE vehicle_id = ? AND status IN ('active', 'locked')
                    """,
                    (vehicle_id,),
                )
            cursor = conn.execute(
                """
                INSERT INTO credentials (cred_type, value, vehicle_id, status, issued_at)
                VALUES ('rfid', ?, ?, 'active', ?)
                """,
                (uid, vehicle_id, datetime.now().isoformat()),
            )
            credential_id = int(cursor.lastrowid)
            conn.execute("DELETE FROM settings WHERE key = 'enroll_mode'")
        cache.refresh_from_db()

    return {
        "vehicle_id": int(vehicle_id),
        "credential_id": credential_id,
        "plate": str(vehicle["plate"]),
        "created_vehicle": created_vehicle,
    }


def create_unit(
    conn: sqlite3.Connection, cache: CacheState, unit_code: str
) -> int:
    """Create a unit with default quotas or return its existing ID."""
    if not unit_code:
        raise ValueError("unit_code must not be empty")

    with cache.db_lock:
        row = conn.execute(
            "SELECT id FROM units WHERE unit_code = ?", (unit_code,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO units (unit_code, quota_car, quota_moto)
                VALUES (?, ?, ?)
                """,
                (unit_code, DEFAULT_QUOTA_CAR, DEFAULT_QUOTA_MOTO),
            )
            unit_id = int(cursor.lastrowid)
        cache.refresh_from_db()
    logger.info("Created unit id=%s", unit_id)
    return unit_id


def create_vehicle(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    unit_id: int,
    plate: str,
    vehicle_type: str,
    plan_expires_at: date | None = None,
    subject_type: str = "resident",
) -> int:
    """Create a vehicle or return the existing ID for its plate."""
    if vehicle_type not in ("car", "moto"):
        raise ValueError("vehicle_type must be 'car' or 'moto'")
    if subject_type not in ("resident", "guest"):
        raise ValueError("subject_type must be 'resident' or 'guest'")
    if not plate:
        raise ValueError("plate must not be empty")

    with cache.db_lock:
        row = conn.execute(
            "SELECT id FROM vehicles WHERE plate = ?", (plate,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        if (
            conn.execute("SELECT 1 FROM units WHERE id = ?", (unit_id,)).fetchone()
            is None
        ):
            raise ValueError(f"Unit not found: {unit_id}")
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO vehicles (
                    plate, vehicle_type, unit_id, subject_type, plan_expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plate,
                    vehicle_type,
                    unit_id,
                    subject_type,
                    plan_expires_at.isoformat() if plan_expires_at is not None else None,
                ),
            )
            vehicle_id = int(cursor.lastrowid)
        cache.refresh_from_db()
    logger.info("Created vehicle id=%s type=%s", vehicle_id, vehicle_type)
    return vehicle_id


def create_credential(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    vehicle_id: int,
    uid: str,
    cred_type: str = "rfid",
) -> int:
    """Create an active credential or return its existing ID for the UID."""
    if cred_type not in ("rfid", "qr"):
        raise ValueError("cred_type must be 'rfid' or 'qr'")
    if not uid:
        raise ValueError("uid must not be empty")

    with cache.db_lock:
        row = conn.execute(
            "SELECT id FROM credentials WHERE value = ?", (uid,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        if (
            conn.execute("SELECT 1 FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            is None
        ):
            raise ValueError(f"Vehicle not found: {vehicle_id}")
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO credentials (
                    cred_type, value, vehicle_id, status, issued_at
                ) VALUES (?, ?, ?, 'active', ?)
                """,
                (cred_type, uid, vehicle_id, datetime.now().isoformat()),
            )
            credential_id = int(cursor.lastrowid)
        cache.refresh_from_db()
    logger.info("Created credential id=%s type=%s", credential_id, cred_type)
    return credential_id


def _set_credential_status(
    conn: sqlite3.Connection,
    cache: CacheState,
    uid: str,
    status: str,
) -> None:
    """Set a credential status, preserving permanently lost credentials."""
    with cache.db_lock:
        row = conn.execute(
            "SELECT id, status FROM credentials WHERE value = ?", (uid,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Credential not found: {uid}")
        if row["status"] == "lost" and status != "lost":
            raise ValueError("A lost credential cannot be reactivated")
        with conn:
            conn.execute(
                "UPDATE credentials SET status = ? WHERE id = ?",
                (status, int(row["id"])),
            )
        cache.refresh_from_db()
    logger.info("Set credential status=%s", status)


def lock_credential(
    conn: sqlite3.Connection, cache: CacheState, uid: str
) -> None:
    """Temporarily lock a credential unless it has been reported lost."""
    _set_credential_status(conn, cache, uid, "locked")


def unlock_credential(
    conn: sqlite3.Connection, cache: CacheState, uid: str
) -> None:
    """Reactivate a credential unless it has been reported lost."""
    _set_credential_status(conn, cache, uid, "active")


def mark_credential_lost(
    conn: sqlite3.Connection, cache: CacheState, uid: str
) -> None:
    """Permanently mark a credential as lost."""
    _set_credential_status(conn, cache, uid, "lost")


def set_vehicle_in_lot(
    conn: sqlite3.Connection,
    cache: CacheState,
    plate: str,
    value: bool,
    *,
    actor: str = "system",
) -> None:
    """Set vehicle occupancy and record a manual-adjustment event."""
    if not isinstance(value, bool):
        raise ValueError("value must be a boolean")
    with cache.db_lock:
        row = conn.execute(
            "SELECT id FROM vehicles WHERE plate = ?", (plate,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Vehicle not found: {plate}")
        vehicle_id = int(row["id"])
        now = datetime.now().isoformat()
        with conn:
            conn.execute(
                "UPDATE vehicles SET in_lot = ? WHERE id = ?",
                (int(value), vehicle_id),
            )
            conn.execute(
                """
                INSERT INTO events (
                    ts, gate, direction, vehicle_id, result, reason_code, ocr_plate
                ) VALUES (?, 'in', 'in', ?, 'allow', 'manual_adjust', ?)
                """,
                (now, vehicle_id, actor),
            )
        cache.refresh_from_db()
    logger.info("Adjusted vehicle occupancy vehicle_id=%s in_lot=%s", vehicle_id, value)


def reset_all_occupancy(conn: sqlite3.Connection, cache: CacheState) -> int:
    """Reset every vehicle to outside-the-lot for an integration test run.

    This intentionally changes only the occupancy flags; vehicle records,
    credentials, and event history are preserved.
    """
    with cache.db_lock:
        with conn:
            cursor = conn.execute("UPDATE vehicles SET in_lot = 0 WHERE in_lot = 1")
        cache.refresh_from_db()
    count = int(cursor.rowcount)
    logger.info("Reset occupancy for %s vehicle(s)", count)
    return count


def set_vehicle_plan_expiry(
    conn: sqlite3.Connection,
    cache: CacheState,
    plate: str,
    expiry: date | None,
) -> None:
    """Set or clear a vehicle plan expiration date."""
    with cache.db_lock:
        row = conn.execute(
            "SELECT id FROM vehicles WHERE plate = ?", (plate,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Vehicle not found: {plate}")
        with conn:
            conn.execute(
                "UPDATE vehicles SET plan_expires_at = ? WHERE id = ?",
                (expiry.isoformat() if expiry is not None else None, int(row["id"])),
            )
        cache.refresh_from_db()
    logger.info("Updated plan expiry for vehicle_id=%s", int(row["id"]))


def _create_user(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    role: str,
    unit_id: int | None,
    username: str,
    password: str,
) -> int:
    """Create a password-authenticated user and refresh the shared cache."""
    if not username:
        raise ValueError("username must not be empty")
    if len(password) < 6:
        raise ValueError("password must be at least 6 characters")

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode()
    with cache.db_lock:
        if conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone() is not None:
            raise ValueError("username already exists")
        if unit_id is not None and conn.execute(
            "SELECT 1 FROM units WHERE id = ?", (unit_id,)
        ).fetchone() is None:
            raise ValueError(f"Unit not found: {unit_id}")
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (role, unit_id, username, password_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (role, unit_id, username, password_hash),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            if "users.username" not in str(error):
                raise
            raise ValueError("username already exists") from error
        cache.refresh_from_db()
    logger.info("Created %s user id=%s username=%s", role, user_id, username)
    return user_id


def create_manager_user(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    username: str,
    password: str,
) -> int:
    """Create a manager user with a bcrypt-hashed password."""
    return _create_user(
        conn,
        cache,
        role="manager",
        unit_id=None,
        username=username,
        password=password,
    )


def create_resident_user(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    unit_id: int,
    username: str,
    password: str,
) -> int:
    """Create a resident user linked to a unit."""
    return _create_user(
        conn,
        cache,
        role="resident",
        unit_id=unit_id,
        username=username,
        password=password,
    )


def list_users_with_units(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List resident and manager accounts for the management page."""
    return conn.execute(
        """
        SELECT u.id, u.username, u.role, u.unit_id, n.unit_code,
               u.telegram_chat_id, u.link_code, u.link_code_expires_at
        FROM users AS u LEFT JOIN units AS n ON n.id = u.unit_id
        ORDER BY u.role, u.username
        """
    ).fetchall()


def create_telegram_link_code(
    conn: sqlite3.Connection, user_id: int, *, duration_minutes: int = 15
) -> str:
    """Create a one-time Telegram link code for a resident."""
    if duration_minutes < 1 or duration_minutes > 60:
        raise ValueError("Thời hạn mã liên kết không hợp lệ.")
    row = conn.execute(
        "SELECT role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None or row["role"] != "resident":
        raise ValueError("Chỉ tài khoản cư dân mới có mã liên kết Telegram.")
    code = secrets.token_urlsafe(8)
    expires = datetime.now() + timedelta(minutes=duration_minutes)
    with conn:
        conn.execute(
            "UPDATE users SET link_code = ?, link_code_expires_at = ?, telegram_chat_id = NULL WHERE id = ?",
            (code, expires.isoformat(), user_id),
        )
    return code


def clear_telegram_link(conn: sqlite3.Connection, user_id: int) -> None:
    """Remove a resident's Telegram link and any pending code."""
    with conn:
        conn.execute(
            "UPDATE users SET telegram_chat_id = NULL, link_code = NULL, link_code_expires_at = NULL WHERE id = ? AND role = 'resident'",
            (user_id,),
        )


def queue_telegram_test_notification(
    conn: sqlite3.Connection, *, recipient_user_id: int, manager_user_id: int
) -> int:
    """Queue a harmless test message for one linked resident."""
    manager = conn.execute(
        "SELECT role FROM users WHERE id = ?", (manager_user_id,)
    ).fetchone()
    if manager is None or manager["role"] != "manager":
        raise ValueError("Chỉ quản lý mới được gửi thông báo thử.")
    recipient = conn.execute(
        "SELECT role, telegram_chat_id FROM users WHERE id = ?",
        (recipient_user_id,),
    ).fetchone()
    if (
        recipient is None or recipient["role"] != "resident"
        or not recipient["telegram_chat_id"]
    ):
        raise ValueError("Cư dân chưa liên kết Telegram.")
    with conn:
        cursor = conn.execute(
            """INSERT INTO notifications
               (recipient_user_id, level, content, created_at)
               VALUES (?, 'info', 'SPS: thông báo thử từ trang quản lý.', ?)""",
            (recipient_user_id, datetime.now().isoformat()),
        )
    return int(cursor.lastrowid)


def link_telegram_chat(conn: sqlite3.Connection, code: str, chat_id: str) -> sqlite3.Row:
    """Consume a valid one-time code and bind the Telegram chat to its resident."""
    row = conn.execute(
        "SELECT id, username, unit_id, link_code_expires_at FROM users "
        "WHERE role = 'resident' AND link_code = ?", (code.strip(),)
    ).fetchone()
    if row is None or not row["link_code_expires_at"]:
        raise ValueError("Mã liên kết không hợp lệ hoặc đã hết hạn.")
    try:
        expires = datetime.fromisoformat(str(row["link_code_expires_at"]))
    except ValueError as error:
        raise ValueError("Mã liên kết không hợp lệ hoặc đã hết hạn.") from error
    if expires <= datetime.now():
        raise ValueError("Mã liên kết đã hết hạn.")
    try:
        with conn:
            conn.execute(
                "UPDATE users SET telegram_chat_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
                (str(chat_id), int(row["id"])),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError("Chat Telegram đã liên kết với tài khoản khác.") from error
    return conn.execute("SELECT * FROM users WHERE id = ?", (int(row["id"]),)).fetchone()


def get_user_by_telegram_chat(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row | None:
    """Find the resident bound to a Telegram chat."""
    return conn.execute(
        "SELECT * FROM users WHERE telegram_chat_id = ? AND role = 'resident'",
        (str(chat_id),),
    ).fetchone()


def list_user_vehicles(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """List vehicles belonging to the resident's apartment."""
    return conn.execute(
        """
        SELECT v.id, v.plate, v.vehicle_type, v.plan_expires_at, v.in_lot,
               c.id AS credential_id, c.value, c.status
        FROM users u JOIN vehicles v ON v.unit_id = u.unit_id
        LEFT JOIN credentials c ON c.vehicle_id = v.id
        WHERE u.id = ? ORDER BY v.plate, c.id
        """, (user_id,),
    ).fetchall()


def list_user_events(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    """Return recent events for vehicles in a resident's apartment."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    return conn.execute(
        """
        SELECT e.ts, v.plate, e.gate, e.result, e.reason_code
        FROM users u JOIN vehicles v ON v.unit_id = u.unit_id
        JOIN events e ON e.vehicle_id = v.id
        WHERE u.id = ? ORDER BY e.ts DESC, e.id DESC LIMIT ?
        """, (user_id, limit),
    ).fetchall()


def list_pending_notifications(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Return unsent Telegram notifications with recipient chat IDs."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    return conn.execute(
        """
        SELECT n.id, n.content, n.image_path, u.telegram_chat_id
        FROM notifications n JOIN users u ON u.id = n.recipient_user_id
        WHERE n.sent_telegram = 0 AND u.telegram_chat_id IS NOT NULL
        ORDER BY n.created_at, n.id LIMIT ?
        """, (limit,),
    ).fetchall()


def mark_notification_sent(conn: sqlite3.Connection, notification_id: int) -> None:
    """Mark one notification delivered to Telegram."""
    with conn:
        conn.execute(
            "UPDATE notifications SET sent_telegram = 1 WHERE id = ?",
            (notification_id,),
        )


def verify_user_password(
    conn: sqlite3.Connection,
    username: str,
    password: str,
) -> sqlite3.Row | None:
    """Return the user row if username exists and password matches."""
    row = conn.execute(
        """
        SELECT id, username, role, unit_id, password_hash
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()
    if row is None or row["password_hash"] is None:
        return None
    try:
        verified = bcrypt.checkpw(
            password.encode("utf-8"), str(row["password_hash"]).encode("utf-8")
        )
    except (ValueError, TypeError):
        logger.exception("Invalid password hash for username=%s", username)
        return None
    return row if verified else None


def get_user_by_id(
    conn: sqlite3.Connection, user_id: int
) -> sqlite3.Row | None:
    """Return a user row by id."""
    return conn.execute(
        "SELECT id, username, role, unit_id FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def get_user_by_username(
    conn: sqlite3.Connection, username: str
) -> sqlite3.Row | None:
    """Return a user row by username."""
    return conn.execute(
        "SELECT id, username, role, unit_id FROM users WHERE username = ?",
        (username,),
    ).fetchone()


def list_units(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all units ordered by unit_code."""
    return conn.execute("SELECT * FROM units ORDER BY unit_code").fetchall()


def get_unit_by_id(
    conn: sqlite3.Connection, unit_id: int
) -> sqlite3.Row | None:
    """Return a unit row by id."""
    return conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()


def count_vehicles_in_unit(conn: sqlite3.Connection, unit_id: int) -> int:
    """Count vehicles registered to one unit."""
    row = conn.execute(
        "SELECT COUNT(*) FROM vehicles WHERE unit_id = ?", (unit_id,)
    ).fetchone()
    return int(row[0])


def get_unit_quota(conn: sqlite3.Connection, unit_id: int) -> tuple[int, int]:
    """Return the car and moto quotas configured for a unit."""
    row = conn.execute(
        "SELECT quota_car, quota_moto FROM units WHERE id = ?", (unit_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unit not found: {unit_id}")
    return int(row["quota_car"]), int(row["quota_moto"])


def get_capacity_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """Return total capacity, occupied spaces, and available spaces."""
    capacity_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'capacity_total'"
    ).fetchone()
    capacity_total = (
        int(capacity_row["value"]) if capacity_row is not None else CAPACITY_TOTAL
    )
    occupied_row = conn.execute(
        "SELECT COUNT(*) FROM vehicles WHERE in_lot = 1"
    ).fetchone()
    occupied = int(occupied_row[0])
    return {
        "capacity_total": capacity_total,
        "occupied": occupied,
        "free": max(0, capacity_total - occupied),
    }


def count_open_alerts(conn: sqlite3.Connection) -> int:
    """Count alerts that a manager has not resolved."""
    row = conn.execute(
        """SELECT COUNT(*) FROM notifications AS n
           JOIN users AS u ON u.id = n.recipient_user_id
           WHERE n.level = 'alert' AND n.resolved_at IS NULL
             AND u.role = 'manager'"""
    ).fetchone()
    return int(row[0])


def get_events_with_images(
    conn: sqlite3.Connection, limit: int = 20
) -> list[sqlite3.Row]:
    """Return recent events with images, including plate and reason details."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    return conn.execute(
        """
        SELECT e.id, e.ts, e.gate, e.direction, e.result, e.reason_code,
               e.image_path, e.ocr_plate, v.plate
        FROM events AS e
        LEFT JOIN vehicles AS v ON v.id = e.vehicle_id
        WHERE e.image_path IS NOT NULL
        ORDER BY e.ts DESC, e.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def list_events(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    """Return recent gate events for the manager history page."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    return conn.execute(
        """
        SELECT e.*, v.plate
        FROM events AS e LEFT JOIN vehicles AS v ON v.id = e.vehicle_id
        ORDER BY e.ts DESC, e.id DESC LIMIT ?
        """, (limit,),
    ).fetchall()


def list_open_alerts(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    """Return unresolved alert notifications for the manager page."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    return conn.execute(
        """
        SELECT n.*, u.username
        FROM notifications AS n LEFT JOIN users AS u ON u.id = n.recipient_user_id
        WHERE n.level = 'alert' AND n.resolved_at IS NULL
          AND u.role = 'manager'
        ORDER BY n.created_at DESC, n.id DESC LIMIT ?
        """, (limit,),
    ).fetchall()


def resolve_alert(
    conn: sqlite3.Connection, alert_id: int, manager_user_id: int
) -> None:
    """Record who acknowledged an alert without changing Telegram delivery state."""
    manager = conn.execute(
        "SELECT role FROM users WHERE id = ?", (manager_user_id,)
    ).fetchone()
    if manager is None or manager["role"] != "manager":
        raise ValueError("Chỉ quản lý mới được xử lý cảnh báo.")
    with conn:
        result = conn.execute(
            """
            UPDATE notifications
            SET resolved_at = ?, resolved_by_user_id = ?
            WHERE id = ? AND level = 'alert' AND resolved_at IS NULL
              AND recipient_user_id IN (SELECT id FROM users WHERE role = 'manager')
            """,
            (datetime.now().isoformat(), manager_user_id, alert_id),
        )
    if result.rowcount != 1:
        raise ValueError("Cảnh báo không tồn tại hoặc đã được xử lý.")


def correct_occupancy_and_open_gate(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    alert_id: int,
    manager_user_id: int,
    vehicle_id: int,
    in_lot: bool,
    gate: str,
) -> int:
    """Resolve an alert, audit occupancy correction, and queue one gate command."""
    if not isinstance(in_lot, bool):
        raise ValueError("Trạng thái xe không hợp lệ.")
    if gate not in ("in", "out"):
        raise ValueError("Cổng không hợp lệ.")
    with cache.db_lock:
        manager = conn.execute(
            "SELECT username, role FROM users WHERE id = ?", (manager_user_id,)
        ).fetchone()
        if manager is None or manager["role"] != "manager":
            raise ValueError("Chỉ quản lý mới được xử lý cảnh báo.")
        alert = conn.execute(
            """SELECT n.id FROM notifications AS n
               JOIN users AS u ON u.id = n.recipient_user_id
               WHERE n.id = ? AND n.level = 'alert'
                 AND n.resolved_at IS NULL AND u.role = 'manager'""",
            (alert_id,),
        ).fetchone()
        if alert is None:
            raise ValueError("Cảnh báo không tồn tại hoặc đã được xử lý.")
        vehicle = conn.execute(
            "SELECT id FROM vehicles WHERE id = ?", (vehicle_id,)
        ).fetchone()
        if vehicle is None:
            raise ValueError("Xe không tồn tại.")
        now = datetime.now().isoformat()
        actor = str(manager["username"] or f"manager:{manager_user_id}")
        with conn:
            conn.execute(
                "UPDATE vehicles SET in_lot = ? WHERE id = ?",
                (int(in_lot), vehicle_id),
            )
            conn.execute(
                """INSERT INTO events
                   (ts, gate, direction, vehicle_id, result, reason_code, ocr_plate)
                   VALUES (?, ?, ?, ?, 'allow', 'manual_adjust', ?)""",
                (now, gate, gate, vehicle_id, actor),
            )
            command = conn.execute(
                """INSERT INTO gate_commands
                   (gate, command, actor, created_at)
                   VALUES (?, 'open_command', ?, ?)""",
                (gate, actor, now),
            )
            conn.execute(
                """UPDATE notifications
                   SET resolved_at = ?, resolved_by_user_id = ?
                   WHERE id = ?""",
                (now, manager_user_id, alert_id),
            )
        cache.refresh_from_db()
    return int(command.lastrowid)


def create_registration_request(
    conn: sqlite3.Connection,
    cache: CacheState,
    *,
    unit_id: int,
    plate: str,
    vehicle_type: str,
    plan_type: str,
    doc_image_path: str | None = None,
) -> int:
    """Create a new pending vehicle-registration request."""
    normalized_plate = plate.strip()
    normalized_plan_type = plan_type.strip()
    if not normalized_plate:
        raise ValueError("Biển số không được để trống.")
    if vehicle_type not in ("car", "moto"):
        raise ValueError("Loại xe phải là ô tô hoặc xe máy.")
    if not normalized_plan_type:
        raise ValueError("Gói đăng ký không được để trống.")

    with cache.db_lock:
        if conn.execute(
            "SELECT 1 FROM units WHERE id = ?", (unit_id,)
        ).fetchone() is None:
            raise ValueError(f"Không tìm thấy căn hộ có mã {unit_id}.")
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO registration_requests (
                    unit_id, plate, vehicle_type, plan_type, doc_image_path,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    unit_id,
                    normalized_plate,
                    vehicle_type,
                    normalized_plan_type,
                    doc_image_path,
                    datetime.now().isoformat(),
                ),
            )
            request_id = int(cursor.lastrowid)
        cache.refresh_from_db()
    logger.info("Created vehicle registration request id=%s", request_id)
    return request_id


def list_registration_requests(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> list[sqlite3.Row]:
    """Return registration requests joined with unit codes, oldest first."""
    query = """
        SELECT r.*, u.unit_code
        FROM registration_requests AS r
        JOIN units AS u ON u.id = r.unit_id
    """
    parameters: tuple[str, ...] = ()
    if status is not None:
        if status not in (
            "pending",
            "approved_waiting_card",
            "active",
            "rejected",
        ):
            raise ValueError("Trạng thái yêu cầu không hợp lệ.")
        query += " WHERE r.status = ?"
        parameters = (status,)
    query += " ORDER BY r.created_at ASC, r.id ASC"
    return conn.execute(query, parameters).fetchall()


def get_registration_request(
    conn: sqlite3.Connection,
    request_id: int,
) -> sqlite3.Row | None:
    """Return one registration request with its unit code."""
    return conn.execute(
        """
        SELECT r.*, u.unit_code
        FROM registration_requests AS r
        JOIN units AS u ON u.id = r.unit_id
        WHERE r.id = ?
        """,
        (request_id,),
    ).fetchone()


def _transition_registration_request(
    conn: sqlite3.Connection,
    cache: CacheState,
    request_id: int,
    *,
    status: str,
    reject_reason: str | None = None,
) -> None:
    """Change a pending request's status exactly once."""
    with cache.db_lock:
        request_row = conn.execute(
            "SELECT status FROM registration_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if request_row is None:
            raise ValueError(f"Không tìm thấy yêu cầu đăng ký số {request_id}.")
        if request_row["status"] != "pending":
            raise ValueError("Chỉ có thể xử lý yêu cầu đang chờ duyệt.")
        with conn:
            cursor = conn.execute(
                """
                UPDATE registration_requests
                SET status = ?, reject_reason = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, reject_reason, request_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Yêu cầu đã được xử lý bởi người khác.")
        cache.refresh_from_db()


def approve_registration_request(
    conn: sqlite3.Connection,
    cache: CacheState,
    request_id: int,
) -> None:
    """Move a pending request to approved-waiting-for-card status."""
    _transition_registration_request(
        conn,
        cache,
        request_id,
        status="approved_waiting_card",
    )
    logger.info("Approved vehicle registration request id=%s", request_id)


def reject_registration_request(
    conn: sqlite3.Connection,
    cache: CacheState,
    request_id: int,
    reason: str,
) -> None:
    """Reject a pending request and save its non-empty rejection reason."""
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("Vui lòng nhập lý do từ chối.")
    _transition_registration_request(
        conn,
        cache,
        request_id,
        status="rejected",
        reject_reason=normalized_reason,
    )
    logger.info("Rejected vehicle registration request id=%s", request_id)


def list_vehicles_with_credentials(
    conn: sqlite3.Connection,
) -> list[dict[str, object]]:
    """Return vehicles with their unit codes and credentials, ordered by plate."""
    vehicle_rows = conn.execute(
        """
        SELECT v.*, u.unit_code
        FROM vehicles AS v
        JOIN units AS u ON u.id = v.unit_id
        ORDER BY v.plate ASC
        """
    ).fetchall()
    if not vehicle_rows:
        return []

    vehicle_ids = [int(row["id"]) for row in vehicle_rows]
    credential_rows = conn.execute(
        """
        SELECT *
        FROM credentials
        ORDER BY id ASC
        """
    ).fetchall()
    credentials_by_vehicle: dict[int, list[sqlite3.Row]] = {
        vehicle_id: [] for vehicle_id in vehicle_ids
    }
    for credential in credential_rows:
        credentials_by_vehicle[int(credential["vehicle_id"])].append(credential)

    return [
        {
            "vehicle": vehicle,
            "unit_code": str(vehicle["unit_code"]),
            "credentials": credentials_by_vehicle[int(vehicle["id"])],
        }
        for vehicle in vehicle_rows
    ]


def get_vehicle_by_id(
    conn: sqlite3.Connection, vehicle_id: int
) -> sqlite3.Row | None:
    """Return a vehicle row by id."""
    return conn.execute(
        "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()


def add_months(start: date, months: int) -> date:
    """Add months to a date, clamping the day to the target month's end."""
    if months <= 0:
        raise ValueError("Số tháng gia hạn phải lớn hơn 0.")
    target_month_index = start.month - 1 + months
    target_year = start.year + target_month_index // 12
    target_month = target_month_index % 12 + 1
    target_day = min(start.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def extend_vehicle_plan(
    conn: sqlite3.Connection,
    cache: CacheState,
    plate: str,
    months: int,
) -> date:
    """Extend a vehicle plan from today or its current unexpired end date."""
    if months <= 0:
        raise ValueError("Số tháng gia hạn phải lớn hơn 0.")

    with cache.db_lock:
        vehicle = conn.execute(
            "SELECT id, plan_expires_at FROM vehicles WHERE plate = ?",
            (plate,),
        ).fetchone()
        if vehicle is None:
            raise ValueError(f"Không tìm thấy xe có biển số {plate}.")

        today = date.today()
        current_expiry = (
            date.fromisoformat(str(vehicle["plan_expires_at"]))
            if vehicle["plan_expires_at"] is not None
            else None
        )
        start_date = (
            current_expiry
            if current_expiry is not None and current_expiry >= today
            else today
        )
        new_expiry = add_months(start_date, months)
        with conn:
            conn.execute(
                "UPDATE vehicles SET plan_expires_at = ? WHERE id = ?",
                (new_expiry.isoformat(), int(vehicle["id"])),
            )
        cache.refresh_from_db()
    logger.info("Extended plan vehicle_id=%s months=%s", int(vehicle["id"]), months)
    return new_expiry
