"""YOLO-based license-plate recognition helpers."""

import logging
import math
import re
import threading
from statistics import mean
from typing import Any


logger = logging.getLogger(__name__)
_PLATE_PATTERN = re.compile(r"^\d{2}(?:[A-Z]{2}|[A-Z]\d?)\d{4,5}$")


def validate_plate(text: str) -> str | None:
    """Normalize OCR output and return it only when it matches a VN plate."""
    raw = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    if _PLATE_PATTERN.fullmatch(raw):
        return raw
    if len(raw) < 6:
        return None
    if len(raw) > 9:
        variants = {raw[1:], raw[:-1], raw[1:-1]}
        matches = [variant for variant in variants if _PLATE_PATTERN.fullmatch(variant)]
        if len(matches) == 1:
            return matches[0]
    return None


def sort_and_join_characters(detections: list[dict[str, str | float]]) -> str:
    """Join character detections left-to-right, with upper rows first."""
    if not detections:
        return ""

    average_height = mean(
        float(detection["y2"]) - float(detection["y1"])
        for detection in detections
    )
    y_positions = sorted(float(detection["y1"]) for detection in detections)
    if y_positions[-1] - y_positions[0] < 0.5 * average_height:
        ordered = sorted(detections, key=lambda detection: float(detection["x1"]))
        return "".join(str(detection["class_name"]) for detection in ordered)

    gaps = [
        (y_positions[index + 1] - y_positions[index], index)
        for index in range(len(y_positions) - 1)
    ]
    _, split_index = max(gaps)
    row_boundary = (y_positions[split_index] + y_positions[split_index + 1]) / 2
    upper_row = sorted(
        (detection for detection in detections
         if float(detection["y1"]) <= row_boundary),
        key=lambda detection: float(detection["x1"]),
    )
    lower_row = sorted(
        (detection for detection in detections
         if float(detection["y1"]) > row_boundary),
        key=lambda detection: float(detection["x1"]),
    )
    ordered = [*upper_row, *lower_row]
    return "".join(str(detection["class_name"]) for detection in ordered)


class OcrEngine:
    """Load YOLO models once and serialize inference across worker threads."""

    def __init__(
        self,
        plate_model_path: str,
        char_model_path: str,
        conf_plate: float,
        conf_char: float,
    ) -> None:
        """Load and warm up both YOLO models."""
        import cv2
        import numpy as np
        from ultralytics import YOLO

        self._cv2: Any = cv2
        self._np: Any = np
        self._conf_plate = conf_plate
        self._conf_char = conf_char
        self._lock = threading.Lock()
        self._plate_model: Any = YOLO(plate_model_path)
        self._char_model: Any = YOLO(char_model_path)

        warmup_image = np.zeros((640, 640, 3), dtype=np.uint8)
        try:
            self._plate_model.predict(warmup_image, verbose=False)
            self._char_model.predict(warmup_image, verbose=False)
        except Exception as error:
            logger.warning("OCR model warmup failed: %s", error)

    def read_plate(self, image_bytes: bytes) -> tuple[str | None, float | None]:
        """Recognize a validated plate from JPEG bytes without raising errors."""
        try:
            buffer = self._np.frombuffer(image_bytes, self._np.uint8)
            image = self._cv2.imdecode(buffer, self._cv2.IMREAD_COLOR)
            if image is None:
                logger.warning("OCR could not decode camera JPEG")
                return None, None

            with self._lock:
                plate_results = self._plate_model.predict(
                    image, conf=self._conf_plate, verbose=False
                )
                if not plate_results or plate_results[0].boxes is None:
                    logger.info("OCR did not detect a license plate")
                    return None, None

                plate_boxes = list(plate_results[0].boxes)
                if not plate_boxes:
                    logger.info("OCR did not detect a license plate")
                    return None, None
                plate_box = max(plate_boxes, key=lambda box: float(box.conf[0]))
                x1, y1, x2, y2 = (
                    float(value) for value in plate_box.xyxy[0].tolist()
                )
                height, width = image.shape[:2]
                padding_x = (x2 - x1) * 0.05
                padding_y = (y2 - y1) * 0.05
                crop_x1 = max(0, math.floor(x1 - padding_x))
                crop_y1 = max(0, math.floor(y1 - padding_y))
                crop_x2 = min(width, math.ceil(x2 + padding_x))
                crop_y2 = min(height, math.ceil(y2 + padding_y))
                crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
                if crop.size == 0:
                    logger.info("OCR detected an empty license-plate crop")
                    return None, None

                char_results = self._char_model.predict(
                    crop, conf=self._conf_char, verbose=False
                )
                if not char_results or char_results[0].boxes is None:
                    logger.info("OCR did not detect plate characters")
                    return None, None

                detections: list[dict[str, str | float]] = []
                for box in char_results[0].boxes:
                    class_id = int(box.cls[0])
                    class_names = self._char_model.names
                    if isinstance(class_names, dict):
                        class_name = str(class_names.get(class_id, class_id))
                    else:
                        class_name = str(class_names[class_id])
                    char_x1, char_y1, char_x2, char_y2 = (
                        float(value) for value in box.xyxy[0].tolist()
                    )
                    detections.append(
                        {
                            "class_name": class_name,
                            "conf": float(box.conf[0]),
                            "x1": char_x1,
                            "y1": char_y1,
                            "x2": char_x2,
                            "y2": char_y2,
                        }
                    )

                if not detections:
                    logger.info("OCR did not detect plate characters")
                    return None, None

                raw_plate = sort_and_join_characters(detections)
                plate = validate_plate(raw_plate)
                if plate is None:
                    logger.info(
                        "OCR output does not match the VN plate pattern: %s",
                        raw_plate,
                    )
                    return None, None
                confidence = mean(float(detection["conf"]) for detection in detections)
                return plate, confidence
        except Exception as error:
            logger.error("OCR failed to read a plate: %s", error)
            return None, None
