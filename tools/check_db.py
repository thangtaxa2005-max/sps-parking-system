"""Quick script để xem trạng thái DB hiện tại."""
from backend.db import connect_database


def main() -> None:
    conn = connect_database()

    print("=== Vehicles ===")
    for row in conn.execute("SELECT id, plate, in_lot FROM vehicles"):
        print(" ", dict(row))

    print("\n=== Credentials ===")
    for row in conn.execute(
        "SELECT id, value, vehicle_id, status FROM credentials"
    ):
        print(" ", dict(row))

    print("\n=== Requests pending ===")
    for row in conn.execute(
        "SELECT id, plate, status FROM registration_requests "
        "WHERE status = 'pending'"
    ):
        print(" ", dict(row))

    print("\n=== Users ===")
    for row in conn.execute(
        "SELECT id, username, role FROM users WHERE username IS NOT NULL"
    ):
        print(" ", dict(row))

    conn.close()


if __name__ == "__main__":
    main()