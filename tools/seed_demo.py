"""Seed demo data: create CAR-1 with the real RFID UID and one moto.

Script an toàn để chạy nhiều lần:
- Reset in_lot = 0 cho mọi xe (để test lại từ đầu).
- Dọn credentials cũ ('UID-CAR', '353610E0') và events tham chiếu.
- Tạo lại credential CAR-1 với UID thẻ thật.
- Tạo MOTO-1 nếu chưa có.
"""
from datetime import date
from pathlib import Path

from backend import api_core
from backend.cache import CacheState
from backend.db import connect_database, initialize_schema

DB_PATH = Path("data/sps.sqlite3")
REAL_CAR_UID = "353610E0"     # UID thẻ thật cho cổng vào
REAL_MOTO_UID = "REPLACE_ME"  # thẻ thứ 2 nếu có; để nguyên nếu chưa


def wipe_credentials(conn, uids: tuple[str, ...]) -> None:
    """Xóa credentials và events tham chiếu tới chúng (theo thứ tự an toàn)."""
    placeholders = ",".join("?" for _ in uids)
    # 1. Xóa events tham chiếu credential sắp xóa
    conn.execute(
        f"DELETE FROM events WHERE credential_id IN "
        f"(SELECT id FROM credentials WHERE value IN ({placeholders}))",
        uids,
    )
    # 2. Xóa credentials
    conn.execute(
        f"DELETE FROM credentials WHERE value IN ({placeholders})",
        uids,
    )
    conn.commit()


def main() -> None:
    conn = connect_database(DB_PATH)
    initialize_schema(conn)
    cache = CacheState(conn)

    # Reset trạng thái in_lot về 0 cho mọi xe để test lại từ đầu.
    conn.execute("UPDATE vehicles SET in_lot = 0")
    conn.commit()
    cache.refresh_from_db()
    print("Đã reset in_lot = 0 cho mọi xe")

    unit_id = api_core.create_unit(conn, cache, "TEST-UNIT")
    print(f"Unit TEST-UNIT id={unit_id}")

    # --- CAR-1 ---
    car_id = api_core.create_vehicle(
        conn, cache,
        unit_id=unit_id, plate="29B155555", vehicle_type="car",
        plan_expires_at=date(2099, 12, 31),
    )

    # Dọn mọi credential cũ của CAR-1 (UID-CAR từ seed giai đoạn 2, hoặc
    # REAL_CAR_UID từ lần chạy trước) + events tham chiếu.
    wipe_credentials(conn, ("UID-CAR", REAL_CAR_UID))

    # Tạo credential mới sạch với UID thẻ thật
    api_core.create_credential(
        conn, cache, vehicle_id=car_id, uid=REAL_CAR_UID
    )
    print(f"CAR-1 ready: plate=CAR-1 uid={REAL_CAR_UID}")

    # --- MOTO-1 ---
    moto_id = api_core.create_vehicle(
        conn, cache,
        unit_id=unit_id, plate="MOTO-1", vehicle_type="moto",
        plan_expires_at=date(2099, 12, 31),
    )
    try:
        api_core.create_credential(
            conn, cache, vehicle_id=moto_id, uid="UID-MOTO"
        )
    except Exception as exc:
        print(f"MOTO credential skipped: {exc}")
    print("MOTO-1 ready: plate=MOTO-1 uid=UID-MOTO")

    # --- Báo cáo ---
    print("\nCredentials trong DB:")
    rows = conn.execute(
        "SELECT c.value, c.status, v.plate "
        "FROM credentials c JOIN vehicles v ON v.id = c.vehicle_id "
        "ORDER BY v.plate, c.value"
    ).fetchall()
    for row in rows:
        print(f"  {row['plate']:8s} uid={row['value']:15s} status={row['status']}")

    print(f"\nFree slots: {cache.free_total}/{cache.capacity_total}")
    conn.close()


if __name__ == "__main__":
    main()