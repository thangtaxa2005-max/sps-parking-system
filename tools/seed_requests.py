"""Seed sample pending vehicle-registration requests for the web demo."""

from backend import api_core
from backend.cache import CacheState
from backend.config import DB_PATH
from backend.db import connect_database, initialize_schema


SAMPLE_REQUESTS: tuple[tuple[str, str, str], ...] = (
    ("29B155555", "car", "year"),
    ("29B12345", "moto", "month"),
    ("30A99999", "car", "year"),
)


def main() -> None:
    """Create missing sample requests and print the resulting request list."""
    connection = connect_database(DB_PATH)
    try:
        initialize_schema(connection)
        cache = CacheState(connection)
        unit_id = api_core.create_unit(connection, cache, "TEST-UNIT")
        pending_plates = {
            str(row["plate"])
            for row in api_core.list_registration_requests(
                connection, status="pending"
            )
        }

        for plate, vehicle_type, plan_type in SAMPLE_REQUESTS:
            if plate in pending_plates:
                print(f"Đã có yêu cầu chờ duyệt: {plate} (bỏ qua)")
                continue
            api_core.create_registration_request(
                connection,
                cache,
                unit_id=unit_id,
                plate=plate,
                vehicle_type=vehicle_type,
                plan_type=plan_type,
            )
            pending_plates.add(plate)
            print(f"Đã tạo yêu cầu: {plate}")

        print("\nDanh sách yêu cầu chờ duyệt:")
        for request in api_core.list_registration_requests(
            connection, status="pending"
        ):
            print(
                f"  {request['unit_code']:12s} {request['plate']:12s} "
                f"{request['vehicle_type']:4s} {request['plan_type']}"
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
