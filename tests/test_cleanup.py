from datetime import datetime, timedelta
from pathlib import Path

from backend import cleanup


def test_cleanup_removes_expired_normal_image(db_connection, monkeypatch):
    root = Path("data") / "_cleanup_test_images"
    root.mkdir()
    path = root / "old.jpg"
    path.write_bytes(b"jpeg")
    monkeypatch.setattr(cleanup, "IMAGE_DIR", root)
    old = datetime.now() - timedelta(days=cleanup.IMAGE_RETENTION_DAYS + 1)
    db_connection.execute(
        "INSERT INTO events (ts, gate, direction, result, reason_code, image_path) VALUES (?, 'in', 'in', 'allow', 'ok', ?)",
        (old.isoformat(), "data/_cleanup_test_images/old.jpg"),
    )
    db_connection.commit()
    try:
        assert cleanup.cleanup_images(db_connection, now=datetime.now()) == 1
        assert not path.exists()
        assert db_connection.execute("SELECT image_path FROM events").fetchone()[0] is None
    finally:
        root.rmdir()


def test_cleanup_keeps_recent_mismatch_image(db_connection, monkeypatch):
    root = Path("data") / "_cleanup_test_images"
    root.mkdir()
    path = root / "alert.jpg"
    path.write_bytes(b"jpeg")
    monkeypatch.setattr(cleanup, "IMAGE_DIR", root)
    old = datetime.now() - timedelta(days=cleanup.IMAGE_RETENTION_DAYS + 1)
    db_connection.execute(
        "INSERT INTO events (ts, gate, direction, result, reason_code, image_path, plate_mismatch) VALUES (?, 'in', 'in', 'allow', 'ok', ?, 1)",
        (old.isoformat(), "data/_cleanup_test_images/alert.jpg"),
    )
    db_connection.commit()
    try:
        assert cleanup.cleanup_images(db_connection, now=datetime.now()) == 0
        assert path.exists()
    finally:
        path.unlink()
        root.rmdir()
