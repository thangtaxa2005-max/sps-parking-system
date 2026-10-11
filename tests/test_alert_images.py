"""Image alerts must remain attached to the originating tap."""

import sqlite3
from datetime import datetime

from backend import api_core
from backend.cache import CacheState
from backend.decision import Decision
from backend.handlers import (
    _attach_image_to_event_sync,
    _record_ocr_result_sync,
    _record_tap_and_alert,
)
from backend.ws_session import SessionManager


def test_image_and_ocr_stay_on_original_tap(
    db_connection: sqlite3.Connection, cache: CacheState
) -> None:
    api_core.create_manager_user(
        db_connection, cache, username="image-manager", password="secret1"
    )
    session = SessionManager(db_connection, cache)
    credential_id = cache.credential_ids["UID-CAR"]

    def record(decision: Decision) -> int:
        return _record_tap_and_alert(
            db_connection, cache.db_lock, gate="in", uid="UID-CAR",
            credential_id=credential_id, decision=decision, now=datetime.now(),
            t_tap=1, t_received=1.0, t_sent=1.1,
        )

    original_id = record(Decision(False, "already_in_lot", "", True))
    later_id = record(Decision(True, "ok", "", False))
    image_path = "data/images/original.jpg"
    _attach_image_to_event_sync(session, original_id, image_path)
    _record_ocr_result_sync(session, original_id, "18A41083", 0.91)

    original = db_connection.execute(
        "SELECT image_path, ocr_plate, plate_mismatch FROM events WHERE id = ?",
        (original_id,),
    ).fetchone()
    later = db_connection.execute(
        "SELECT image_path, ocr_plate FROM events WHERE id = ?", (later_id,)
    ).fetchone()
    alerts = db_connection.execute(
        "SELECT event_id, image_path FROM notifications ORDER BY id"
    ).fetchall()
    assert tuple(original) == (image_path, "18A41083", 1)
    assert tuple(later) == (None, None)
    assert len(alerts) == 2
    assert all(tuple(row) == (original_id, image_path) for row in alerts)
