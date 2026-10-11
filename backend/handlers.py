"""WebSocket message handlers for gate events."""

import asyncio
import _thread
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any

from backend import api_core
from backend.cache import CacheState
from backend.camera import CameraManager, save_image
from backend.decision import Decision, decide
from backend.events import confirm_pass, record_no_entry
from backend.ocr import OcrEngine
from backend.ws_session import SessionManager


logger = logging.getLogger(__name__)

_clock_override: datetime | None = None

_ALERT_MESSAGES = {
    "card_locked": "Thẻ của xe {plate} vừa bị quẹt khi đang tạm khóa",
    "card_lost": "Thẻ báo mất của xe {plate} vừa bị quẹt",
    "already_in_lot": "Xe {plate} đã ở trong bãi mà còn quẹt vào",
    "not_in_lot": "Xe {plate} chưa vào bãi mà quẹt ra",
}


def decision_enrolled() -> Decision:
    """Return a non-opening decision for a successful enrollment tap."""
    return Decision(False, "enrolled", "Đã đăng ký", False)


def _try_enroll(
    session: SessionManager,
    cache: CacheState,
    uid: str,
    gate: str,
) -> dict | None:
    """Attach a new UID when enrollment is active; otherwise use normal flow."""
    try:
        if uid in cache.credentials:
            return None
        return api_core.attach_credential_from_enroll(
            session.connection, cache, uid=uid, gate=gate
        )
    except ValueError:
        logger.warning("Enroll failed for uid=%s gate=%s", uid, gate)
        return None
    except Exception:
        logger.exception("Enroll error for uid=%s gate=%s", uid, gate)
        return None


def get_now() -> datetime:
    """Return overridden test clock or real current time."""
    return _clock_override if _clock_override is not None else datetime.now()


def set_clock_override(dt: datetime | None) -> None:
    """Set or clear the test clock override."""
    global _clock_override
    _clock_override = dt


def _record_tap_and_alert(
    connection: sqlite3.Connection,
    lock: _thread.RLock,
    *,
    gate: str,
    uid: str,
    credential_id: int | None,
    decision: Decision,
    now: datetime,
    t_tap: int,
    t_received: float,
    t_sent: float,
) -> int:
    """Persist a tap event and any associated alert notifications."""
    with lock, connection:
        vehicle_id: int | None = None
        plate: str | None = None
        unit_id: int | None = None
        if credential_id is not None:
            vehicle = connection.execute(
                """
                SELECT v.id, v.plate, v.unit_id
                FROM credentials AS c
                JOIN vehicles AS v ON v.id = c.vehicle_id
                WHERE c.id = ?
                """,
                (credential_id,),
            ).fetchone()
            if vehicle is not None:
                vehicle_id = int(vehicle["id"])
                plate = str(vehicle["plate"])
                unit_id = int(vehicle["unit_id"])

        event_cursor = connection.execute(
            """
            INSERT INTO events (
                ts, gate, direction, credential_id, vehicle_id, result,
                reason_code, t_tap, t_sent, t_received
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(),
                gate,
                gate,
                credential_id,
                vehicle_id,
                "allow" if decision.allow else "deny",
                decision.reason_code,
                str(t_tap),
                repr(t_sent),
                repr(t_received),
            ),
        )
        event_id = int(event_cursor.lastrowid)
        if not decision.alert or plate is None:
            return event_id

        template = _ALERT_MESSAGES.get(decision.reason_code)
        if template is None:
            return event_id
        content = template.format(plate=plate)
        recipients = connection.execute(
            """
            SELECT id FROM users
            WHERE role = 'manager'
               OR (role = 'resident' AND unit_id = ?)
            """,
            (unit_id,),
        ).fetchall()
        connection.executemany(
            """
            INSERT INTO notifications (
                recipient_user_id, level, content, created_at, sent_telegram, event_id
            ) VALUES (?, 'alert', ?, ?, 0, ?)
            """,
            [(int(row["id"]), content, now.isoformat(), event_id) for row in recipients],
        )
        return event_id


def _record_stuck_open(
    connection: sqlite3.Connection,
    lock: _thread.RLock,
    *,
    gate: str,
    duration_s: float,
    now: datetime,
) -> None:
    """Create a manager alert for a barrier held open too long."""
    content = f"Cổng {gate} mở quá {duration_s:g} giây"
    with lock, connection:
        managers = connection.execute(
            "SELECT id FROM users WHERE role = 'manager'"
        ).fetchall()
        connection.executemany(
            """
            INSERT INTO notifications (
                recipient_user_id, level, content, created_at, sent_telegram
            ) VALUES (?, 'alert', ?, ?, 0)
            """,
            [
                (int(row["id"]), content, now.isoformat())
                for row in managers
            ],
        )


def _confirm_pass_locked(
    session: SessionManager,
    gate: str,
    credential_id: int,
    now: datetime,
    cache: CacheState,
) -> None:
    """Serialize a passage update with all other access to the SQLite connection."""
    with session.db_lock:
        confirm_pass(gate, credential_id, now, session.connection, cache)


def _record_no_entry_locked(
    session: SessionManager, gate: str, credential_id: int, now: datetime
) -> None:
    """Serialize a no-entry event with all other access to SQLite."""
    with session.db_lock:
        record_no_entry(gate, credential_id, now, session.connection)


async def _persist_tap(
    session: SessionManager,
    *,
    gate: str,
    uid: str,
    credential_id: int | None,
    decision: Decision,
    now: datetime,
    t_tap: int,
    t_received: float,
    t_sent: float,
) -> int | None:
    """Persist a tap event without propagating background failures."""
    try:
        return await asyncio.to_thread(
            _record_tap_and_alert,
            session.connection,
            session.db_lock,
            gate=gate,
            uid=uid,
            credential_id=credential_id,
            decision=decision,
            now=now,
            t_tap=t_tap,
            t_received=t_received,
            t_sent=t_sent,
        )
    except Exception:
        logger.exception("Background tap persistence failed for gate=%s", gate)
        return None


def _attach_image_to_event_sync(
    session: SessionManager, event_id: int, image_path: str
) -> None:
    """Attach the captured image to its exact event and unsent alerts."""
    with session.db_lock, session.connection:
        session.connection.execute(
            "UPDATE events SET image_path = ? WHERE id = ?",
            (image_path, event_id),
        )
        session.connection.execute(
            """UPDATE notifications SET image_path = ?
               WHERE event_id = ? AND sent_telegram = 0""",
            (image_path, event_id),
        )


async def _capture_and_attach(
    session: SessionManager,
    camera: CameraManager,
    ocr_engine: OcrEngine | None,
    gate: str,
    event_id: int,
) -> None:
    """Capture a gate image and attach it to the recorded tap event."""
    try:
        image_bytes = await asyncio.to_thread(camera.capture, gate)
        if image_bytes is None:
            logger.warning("No camera frame available for gate=%s", gate)
            return
        path = save_image(image_bytes, gate, datetime.now())
        if path is None:
            return
        await asyncio.to_thread(_attach_image_to_event_sync, session, event_id, str(path))
        if ocr_engine is not None:
            plate_text, confidence = await asyncio.to_thread(
                ocr_engine.read_plate, image_bytes
            )
            await _record_ocr_result(
                session, event_id, plate_text, confidence
            )
    except Exception:
        logger.exception("Background image capture failed for gate=%s", gate)


def _record_ocr_result_sync(
    session: SessionManager,
    event_id: int,
    plate_text: str | None,
    confidence: float | None,
) -> None:
    """Update the recorded event and create mismatch alerts with its image."""
    with session.db_lock, session.connection:
        event = session.connection.execute(
            """
            SELECT id, credential_id, image_path FROM events WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            logger.warning(
                "No event found for OCR result event_id=%s",
                event_id,
            )
            return

        credential_id = event["credential_id"]
        registered_plate: str | None = None
        unit_id: int | None = None
        if credential_id is not None:
            vehicle = session.connection.execute(
                """
                SELECT v.plate, v.unit_id
                FROM credentials AS c
                JOIN vehicles AS v ON v.id = c.vehicle_id
                WHERE c.id = ?
                """,
                (credential_id,),
            ).fetchone()
            if vehicle is not None:
                registered_plate = str(vehicle["plate"])
                unit_id = int(vehicle["unit_id"])

        mismatch = 0
        if plate_text is not None and registered_plate is not None:
            import Levenshtein

            mismatch = int(Levenshtein.distance(plate_text, registered_plate) > 1)

        session.connection.execute(
            """
            UPDATE events
            SET ocr_plate = ?, ocr_confidence = ?, plate_mismatch = ?
            WHERE id = ?
            """,
            (plate_text, confidence, mismatch, event_id),
        )

        if plate_text is None:
            logger.info("OCR returned no plate for event_id=%s", event_id)
            return
        if mismatch == 0 or registered_plate is None:
            return

        content = (
            f"Biển số đọc được ({plate_text}) khác biển đăng ký "
            f"({registered_plate})"
        )
        recipients = session.connection.execute(
            """
            SELECT id FROM users
            WHERE role = 'manager'
               OR (role = 'resident' AND unit_id = ?)
            """,
            (unit_id,),
        ).fetchall()
        session.connection.executemany(
            """
            INSERT INTO notifications (
                recipient_user_id, level, content, image_path,
                created_at, sent_telegram, event_id
            ) VALUES (?, 'alert', ?, ?, ?, 0, ?)
            """,
            [
                (int(row["id"]), content, event["image_path"], get_now().isoformat(), event_id)
                for row in recipients
            ],
        )


async def _record_ocr_result(
    session: SessionManager,
    event_id: int,
    plate_text: str | None,
    confidence: float | None,
) -> None:
    """Persist OCR output in a worker thread without blocking the event loop."""
    try:
        await asyncio.to_thread(
            _record_ocr_result_sync,
            session,
            event_id,
            plate_text,
            confidence,
        )
    except Exception:
        logger.exception("Failed to persist OCR result for event_id=%s", event_id)


async def _persist_then_capture(
    session: SessionManager,
    camera: CameraManager,
    ocr_engine: OcrEngine | None,
    *,
    gate: str,
    uid: str,
    credential_id: int | None,
    decision: Decision,
    now: datetime,
    t_tap: int,
    t_received: float,
    t_sent: float,
) -> None:
    """Persist the tap, then attach image and OCR using its returned event ID."""
    event_id = await _persist_tap(
        session,
        gate=gate,
        uid=uid,
        credential_id=credential_id,
        decision=decision,
        now=now,
        t_tap=t_tap,
        t_received=t_received,
        t_sent=t_sent,
    )
    if event_id is not None:
        await _capture_and_attach(session, camera, ocr_engine, gate, event_id)


async def handle_tap(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
    camera: CameraManager,
    ocr_engine: OcrEngine | None = None,
) -> None:
    """Decide a card tap, respond first, then persist it in the background."""
    uid_value = payload.get("uid")
    tap_value = payload.get("t_tap")
    if (
        not isinstance(uid_value, str)
        or isinstance(tap_value, bool)
        or not isinstance(tap_value, int)
    ):
        logger.warning("Ignoring malformed tap message for gate=%s", gate)
        return
    if payload.get("gate", gate) != gate:
        logger.warning("Ignoring tap with mismatched gate header=%s", gate)
        return

    uid = uid_value
    t_tap = tap_value
    t_received = time.monotonic()
    now = get_now()
    enroll_result = await asyncio.to_thread(
        _try_enroll, session, cache, uid, gate
    )
    if enroll_result is not None:
        decision = decision_enrolled()
        message = {
            "type": "decision",
            "allow": False,
            "reason_code": decision.reason_code,
            "oled_message": decision.oled_message,
            "alert": False,
            "free_total": cache.free_total,
        }
        sent = await session.send_to(gate, message)
        t_sent = time.monotonic()
        logger.info(
            "ENROLL gate=%s uid=%s vehicle_id=%s plate=%s allow=false",
            gate, uid, enroll_result["vehicle_id"], enroll_result["plate"],
        )
        session.spawn_background(
            _persist_then_capture(
                session, camera, ocr_engine,
                gate=gate, uid=uid,
                credential_id=enroll_result["credential_id"],
                decision=decision,
                now=now, t_tap=t_tap,
                t_received=t_received, t_sent=t_sent,
            )
        )
        return
    decision = decide(gate, uid, now, cache)
    free_total = cache.free_total
    message = {
        "type": "decision",
        "allow": decision.allow,
        "reason_code": decision.reason_code,
        "oled_message": decision.oled_message,
        "alert": decision.alert,
        "free_total": free_total,
    }
    sent = await session.send_to(gate, message)
    t_sent = time.monotonic()
    latency_ms = (t_sent - t_received) * 1000
    logger.info(
        "DECISION gate=%s uid=%s allow=%s reason=%s latency_ms=%.1f",
        gate,
        uid,
        decision.allow,
        decision.reason_code,
        latency_ms,
    )

    credential_id = cache.credential_ids.get(uid)
    if sent and decision.allow and credential_id is not None:
        session.last_allow_credential[gate] = credential_id

    # Ghi sự kiện + chụp ảnh tuần tự trong CÙNG 1 task nền, không chặn handle_tap.
    # send_to decision đã xong ở trên, nên mục tiêu <500ms vẫn đảm bảo.
    session.spawn_background(
        _persist_then_capture(
            session,
            camera,
            ocr_engine,
            gate=gate,
            uid=uid,
            credential_id=credential_id,
            decision=decision,
            now=now,
            t_tap=t_tap,
            t_received=t_received,
            t_sent=t_sent,
        )
    )


async def handle_pass_confirmed(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
) -> None:
    """Confirm the latest allowed credential passage for a gate."""
    del payload
    credential_id = session.last_allow_credential.get(gate)
    if credential_id is None:
        logger.warning("No allowed credential to confirm for gate=%s", gate)
        return
    try:
        await asyncio.to_thread(
            _confirm_pass_locked,
            session,
            gate,
            credential_id,
            get_now(),
            cache,
        )
    except Exception:
        logger.exception("Failed to confirm passage for gate=%s", gate)
        return
    session.last_allow_credential.pop(gate, None)


async def handle_no_entry_timeout(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
) -> None:
    """Record an entry timeout without changing occupancy."""
    del payload, cache
    credential_id = session.last_allow_credential.get(gate)
    if credential_id is None:
        logger.warning("No allowed credential for no-entry timeout at gate=%s", gate)
        return
    try:
        await asyncio.to_thread(
            _record_no_entry_locked,
            session,
            gate,
            credential_id,
            get_now(),
        )
    except Exception:
        logger.exception("Failed to record no-entry timeout for gate=%s", gate)
        return
    session.last_allow_credential.pop(gate, None)


async def handle_obstruction(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
) -> None:
    """Log an obstruction message without writing database state."""
    del session, payload, cache
    logger.warning("Obstruction detected at gate=%s", gate)


async def handle_stuck_open(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
) -> None:
    """Persist manager notifications for a gate held open too long."""
    del cache
    duration = payload.get("duration_s")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        logger.warning("Ignoring malformed stuck_open message for gate=%s", gate)
        return
    now = get_now()
    try:
        await asyncio.to_thread(
            _record_stuck_open,
            session.connection,
            session.db_lock,
            gate=gate,
            duration_s=float(duration),
            now=now,
        )
    except Exception:
        logger.exception("Failed to record stuck-open alert for gate=%s", gate)
        return
    logger.warning("Gate stuck open: gate=%s duration_s=%s", gate, duration)


async def dispatch_message(
    session: SessionManager,
    gate: str,
    payload: dict[str, Any],
    cache: CacheState,
    camera: CameraManager,
    ocr_engine: OcrEngine | None = None,
) -> None:
    """Dispatch a decoded WebSocket message to its handler."""
    message_type = payload.get("type")
    if message_type == "ping":
        return
    if message_type == "tap":
        await handle_tap(session, gate, payload, cache, camera, ocr_engine)
        return
    handlers = {
        "pass_confirmed": handle_pass_confirmed,
        "no_entry_timeout": handle_no_entry_timeout,
        "obstruction": handle_obstruction,
        "stuck_open": handle_stuck_open,
    }
    handler = handlers.get(message_type) if isinstance(message_type, str) else None
    if handler is None:
        logger.warning("Ignoring unsupported WebSocket message type=%r", message_type)
        return
    await handler(session, gate, payload, cache)
