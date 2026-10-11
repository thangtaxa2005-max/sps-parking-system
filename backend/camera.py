"""IP camera frame polling and event image storage.

Đọc ảnh JPEG từ endpoint snapshot của IP Webcam (/shot.jpg) mỗi 200ms.
Không dùng FFMPEG nên tránh được lỗi timeout của OpenCV với MJPEG stream.
"""

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from _thread import LockType
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.config import (
    CAMERA_CONNECT_TIMEOUT_S,
    CAMERA_FRAME_STALE_S,
    CAMERA_READ_TIMEOUT_S,
    IMAGE_DIR,
)


logger = logging.getLogger(__name__)
_RECONNECT_DELAY_S = 3
_POLL_INTERVAL_S = 0.2


@dataclass
class _CameraState:
    gate: str
    url: str
    lock: LockType = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    latest_frame: bytes | None = None
    latest_frame_time: float = 0.0
    thread: threading.Thread | None = None
    connected: bool = False
    unavailable_warning_logged: bool = False


class CameraManager:
    """Poll the latest JPEG snapshot from one IP camera per gate."""

    def __init__(self, url_in: str, url_out: str) -> None:
        """Create camera state for the entry and exit gates."""
        self._cameras = {
            "in": _CameraState("in", url_in.strip()),
            "out": _CameraState("out", url_out.strip()),
        }

    def start(self) -> None:
        """Start a daemon poller for each configured camera."""
        for state in self._cameras.values():
            if not state.url:
                continue
            if state.thread is not None and state.thread.is_alive():
                continue
            state.stop_event = threading.Event()
            state.thread = threading.Thread(
                target=self._read_camera,
                args=(state,),
                name=f"camera-{state.gate}",
                daemon=True,
            )
            state.thread.start()

    def stop(self) -> None:
        """Stop camera pollers and wait for their threads to exit."""
        for state in self._cameras.values():
            state.stop_event.set()

        join_timeout = CAMERA_CONNECT_TIMEOUT_S + CAMERA_READ_TIMEOUT_S + 1
        for state in self._cameras.values():
            thread = state.thread
            if thread is None:
                continue
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                logger.error(
                    "Camera poller did not stop within %.1f seconds for gate=%s",
                    join_timeout,
                    state.gate,
                )

    def capture(self, gate: str) -> bytes | None:
        """Return a fresh JPEG frame for a gate, if one is available."""
        state = self._cameras.get(gate)
        if state is None or not state.url:
            return None

        with state.lock:
            frame = state.latest_frame
            frame_time = state.latest_frame_time
            connected = state.connected
        if (
            not connected
            or frame is None
            or time.time() - frame_time > CAMERA_FRAME_STALE_S
        ):
            self._warn_unavailable(state, "no fresh frame is available")
            return None
        return frame

    def _warn_unavailable(self, state: _CameraState, reason: str) -> None:
        """Log only the first unavailable warning between successful frames."""
        with state.lock:
            if state.unavailable_warning_logged:
                return
            state.unavailable_warning_logged = True
            state.connected = False
        logger.warning("Camera unavailable for gate=%s: %s", state.gate, reason)

    def _read_camera(self, state: _CameraState) -> None:
        """Poll the snapshot URL every 200ms until the stop event is set."""
        import urllib.request  # local import để thread-safe khi reload

        first_attempt = True
        while not state.stop_event.is_set():
            request = urllib.request.Request(
                state.url,
                headers={"User-Agent": "SPS-Backend/1.0"},
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=CAMERA_READ_TIMEOUT_S
                ) as response:
                    data = response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if first_attempt:
                    logger.info(
                        "Camera poll failed for gate=%s: %s", state.gate, error
                    )
                    first_attempt = False
                self._warn_unavailable(state, f"snapshot fetch failed: {error}")
                state.stop_event.wait(_RECONNECT_DELAY_S)
                continue
            except Exception:
                logger.exception(
                    "Unexpected camera error for gate=%s; retrying", state.gate
                )
                self._warn_unavailable(state, "unexpected error")
                state.stop_event.wait(_RECONNECT_DELAY_S)
                continue

            if not data or not data.startswith(b"\xff\xd8"):
                self._warn_unavailable(state, "invalid JPEG payload")
                state.stop_event.wait(_RECONNECT_DELAY_S)
                continue

            with state.lock:
                was_connected = state.connected
                state.latest_frame = data
                state.latest_frame_time = time.time()
                state.unavailable_warning_logged = False
                state.connected = True

            if not was_connected:
                logger.info("Camera connected for gate=%s", state.gate)

            state.stop_event.wait(_POLL_INTERVAL_S)


def save_image(image_bytes: bytes, gate: str, captured_at: datetime) -> Path | None:
    """Save a captured JPEG and return its project-relative image path."""
    if gate not in ("in", "out"):
        logger.error("Cannot save camera image for invalid gate=%r", gate)
        return None

    try:
        filename = f"{captured_at:%Y%m%d_%H%M%S_%f}_{gate}.jpg"
        destination = IMAGE_DIR / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image_bytes)
        project_root = Path(__file__).resolve().parent.parent
        try:
            relative_path = destination.resolve().relative_to(project_root)
        except ValueError:
            relative_path = Path(
                os.path.relpath(destination.resolve(), project_root)
            )
    except Exception:
        logger.exception("Failed to save camera image for gate=%s", gate)
        return None

    logger.info("Saved camera image for gate=%s path=%s", gate, relative_path)
    return relative_path