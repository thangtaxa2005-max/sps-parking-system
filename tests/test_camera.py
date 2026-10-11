"""Tests for snapshot-polling camera handling without physical cameras."""

from __future__ import annotations

import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

import numpy as np

from backend.camera import CameraManager


class FakeResponse:
    """Minimal context manager matching urllib's HTTP response interface."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class CameraManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        fake_payload = np.frombuffer(b"fake jpeg data", dtype=np.uint8).tobytes()
        self.jpeg_bytes = b"\xff\xd8" + fake_payload + b"\xff\xd9"
        self.managers: list[CameraManager] = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.stop()

    def _manager(self, url_in: str, url_out: str = "") -> CameraManager:
        manager = CameraManager(url_in, url_out)
        self.managers.append(manager)
        return manager

    def _wait_for_frame(self, manager: CameraManager) -> bytes | None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            frame = manager.capture("in")
            if frame is not None:
                return frame
            time.sleep(0.05)
        return None

    def test_capture_returns_none_when_url_empty(self) -> None:
        manager = self._manager("", "")
        manager.start()

        self.assertIsNone(manager.capture("in"))

    def test_capture_returns_none_when_not_connected(self) -> None:
        manager = self._manager("http://localhost:9999/video", "")
        with patch(
            "backend.camera.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            manager.start()
            try:
                time.sleep(0.5)
                self.assertIsNone(manager.capture("in"))
            finally:
                manager.stop()

    def test_capture_returns_jpeg_bytes(self) -> None:
        manager = self._manager("http://fake/video", "")
        with patch(
            "backend.camera.urllib.request.urlopen",
            return_value=FakeResponse(self.jpeg_bytes),
        ):
            manager.start()
            try:
                image_bytes = self._wait_for_frame(manager)
            finally:
                manager.stop()

        self.assertIsNotNone(image_bytes)
        assert image_bytes is not None
        self.assertTrue(image_bytes.startswith(b"\xff\xd8"))

    def test_frame_becomes_stale(self) -> None:
        manager = self._manager("http://fake/video", "")
        second_request_started = threading.Event()
        release_second_request = threading.Event()
        call_count = 0

        def fetch_snapshot(*_args: object, **_kwargs: object) -> FakeResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeResponse(self.jpeg_bytes)
            second_request_started.set()
            release_second_request.wait(timeout=2)
            raise urllib.error.URLError("timeout")

        with (
            patch("backend.camera.CAMERA_FRAME_STALE_S", 0.2),
            patch(
                "backend.camera.urllib.request.urlopen",
                side_effect=fetch_snapshot,
            ),
        ):
            manager.start()
            try:
                self.assertIsNotNone(self._wait_for_frame(manager))
                self.assertTrue(second_request_started.wait(timeout=1))
                time.sleep(0.3)
                self.assertIsNone(manager.capture("in"))
            finally:
                release_second_request.set()
                manager.stop()

    def test_stop_terminates_threads(self) -> None:
        manager = self._manager("http://fake/video", "")
        with patch(
            "backend.camera.urllib.request.urlopen",
            return_value=FakeResponse(self.jpeg_bytes),
        ):
            manager.start()
            try:
                self.assertIsNotNone(self._wait_for_frame(manager))
            finally:
                manager.stop()

        self.assertFalse(
            any(
                state.thread is not None and state.thread.is_alive()
                for state in manager._cameras.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
