"""Retention cleanup for captured event images and old alert images."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from backend.config import ALERT_IMAGE_RETENTION_DAYS, IMAGE_DIR, IMAGE_RETENTION_DAYS

logger = logging.getLogger(__name__)


def cleanup_images(connection, *, now: datetime | None = None) -> int:
    """Delete expired event images and clear their database paths.

    Mismatch events are retained for the longer alert retention period.
    Files are only deleted when their resolved path is inside IMAGE_DIR.
    """
    current = now or datetime.now()
    normal_cutoff = current - timedelta(days=IMAGE_RETENTION_DAYS)
    alert_cutoff = current - timedelta(days=ALERT_IMAGE_RETENTION_DAYS)
    root = IMAGE_DIR.resolve()
    rows = connection.execute(
        "SELECT id, image_path, ts, plate_mismatch FROM events WHERE image_path IS NOT NULL"
    ).fetchall()
    removed = 0
    for row in rows:
        try:
            captured = datetime.fromisoformat(str(row["ts"]))
        except ValueError:
            continue
        cutoff = alert_cutoff if row["plate_mismatch"] else normal_cutoff
        if captured > cutoff:
            continue
        path = (root.parent.parent / str(row["image_path"])).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            logger.warning("Skipping image outside retention root: %s", path)
            continue
        if path.is_file():
            path.unlink()
        with connection:
            connection.execute("UPDATE events SET image_path = NULL WHERE id = ?", (int(row["id"]),))
        removed += 1
    return removed
