"""Create a manager user for the SPS demo."""

import getpass
import sys

from backend import api_core
from backend.cache import CacheState
from backend.config import DB_PATH
from backend.db import connect_database, initialize_schema


def main() -> None:
    """Prompt for credentials and create one manager account."""
    connection = connect_database(DB_PATH)
    try:
        initialize_schema(connection)
        cache = CacheState(connection)
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        try:
            user_id = api_core.create_manager_user(
                connection,
                cache,
                username=username,
                password=password,
            )
        except ValueError as error:
            print(f"Lỗi: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        print(f"Đã tạo manager: id={user_id}, username={username}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
