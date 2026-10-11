"""Configuration defaults for the SPS backend."""

import os
from pathlib import Path


CAPACITY_TOTAL = 20
DEFAULT_QUOTA_CAR = 1
DEFAULT_QUOTA_MOTO = 1
OCR_THRESHOLD = 0.8
OCR_CONF_PLATE = 0.65
OCR_CONF_CHAR = 0.35
OCR_MODEL_PLATE_PATH = str(
    Path(__file__).resolve().parent.parent / "models" / "license_plate.pt"
)
OCR_MODEL_CHAR_PATH = str(
    Path(__file__).resolve().parent.parent / "models" / "character_detect.pt"
)
IMAGE_RETENTION_DAYS = 30
ALERT_RETENTION_DAYS = 90
ALERT_IMAGE_RETENTION_DAYS = 90
OPEN_WAIT_SECONDS = 5
PASS_CLEAR_SECONDS = 0.5
STUCK_OPEN_SECONDS = 30
SENSOR_INTERVAL_MS = 55
SENSOR_MEDIAN_SAMPLES = 3
DECISION_TARGET_MS = 20
ENROLL_DURATION_S = 60

CAMERA_URL_IN = os.environ.get(
    "SPS_CAMERA_URL_IN",
    "http://192.168.1.127:8080/shot.jpg",
)
CAMERA_URL_OUT = os.environ.get("SPS_CAMERA_URL_OUT", "")

IMAGE_DIR = Path(
    os.environ.get(
        "SPS_IMAGE_DIR",
        str(Path(__file__).resolve().parent.parent / "data" / "images"),
    )
)

CAMERA_CONNECT_TIMEOUT_S = 5
CAMERA_READ_TIMEOUT_S = 3
CAMERA_FRAME_STALE_S = 2

DB_PATH = Path(
    os.environ.get(
        "SPS_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "sps.sqlite3"),
    )
)
