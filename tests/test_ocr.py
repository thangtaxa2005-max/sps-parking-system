"""Pure tests for license-plate OCR normalization and character ordering."""

from backend.ocr import sort_and_join_characters, validate_plate


def test_validate_plate_bike_4digits() -> None:
    assert validate_plate("29B1234") == "29B1234"


def test_validate_plate_bike_5digits() -> None:
    assert validate_plate("29B12345") == "29B12345"


def test_validate_plate_bike_2letter() -> None:
    assert validate_plate("29AB12345") == "29AB12345"


def test_validate_plate_car_4digits() -> None:
    assert validate_plate("30A1234") == "30A1234"


def test_validate_plate_car_5digits() -> None:
    assert validate_plate("30A12345") == "30A12345"


def test_validate_with_dots_and_dashes() -> None:
    assert validate_plate("29-A 1234") == "29A1234"


def test_validate_with_mixed_whitespace() -> None:
    assert validate_plate("  29-AB   12345  ") == "29AB12345"


def test_validate_too_short() -> None:
    assert validate_plate("29B1") is None


def test_validate_wrong_pattern() -> None:
    assert validate_plate("ABCDEFG") is None


def test_validate_lowercase() -> None:
    assert validate_plate("29b12345") == "29B12345"


def test_validate_too_long_trim() -> None:
    assert validate_plate("X29A12345Y") == "29A12345"


def test_validate_too_long_ambiguous() -> None:
    """2 biến thể cùng khớp → mơ hồ, trả None."""
    assert validate_plate("029AB12345") is None


def _detection(
    character: str,
    x1: float,
    y1: float,
    *,
    width: float = 8,
    height: float = 12,
) -> dict[str, str | float]:
    """Build one character bounding box for ordering tests."""
    return {
        "class_name": character,
        "conf": 0.9,
        "x1": x1,
        "y1": y1,
        "x2": x1 + width,
        "y2": y1 + height,
    }


def test_sort_single_row() -> None:
    detections = [
        _detection("5", 40, 10),
        _detection("2", 10, 11),
        _detection("A", 30, 9),
        _detection("9", 0, 10),
        _detection("B", 20, 10),
    ]
    assert sort_and_join_characters(detections) == "92BA5"


def test_sort_two_rows() -> None:
    detections = [
        _detection("4", 20, 30),
        _detection("2", 10, 10),
        _detection("5", 30, 30),
        _detection("9", 0, 10),
    ]
    assert sort_and_join_characters(detections) == "9245"


def test_sort_empty() -> None:
    assert sort_and_join_characters([]) == ""
