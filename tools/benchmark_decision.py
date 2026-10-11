"""Measure the pure RFID decision path without hardware or network."""

import statistics
import time
from datetime import datetime

from backend.cache import CacheState
from backend.db import initialize_schema
from backend.decision import decide
import sqlite3


def main() -> None:
    now = datetime.now()
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    unit_id = connection.execute(
        "INSERT INTO units (unit_code) VALUES ('BENCH-UNIT')"
    ).lastrowid
    vehicle_id = connection.execute(
        "INSERT INTO vehicles (plate, vehicle_type, unit_id, plan_expires_at) VALUES ('BENCH-1', 'car', ?, '2099-12-31')",
        (unit_id,),
    ).lastrowid
    connection.execute(
        "INSERT INTO credentials (cred_type, value, vehicle_id, status, issued_at) VALUES ('rfid', 'BENCH-UID', ?, 'active', ?)",
        (vehicle_id, "2026-01-01T00:00:00"),
    )
    connection.commit()
    cache = CacheState(connection)
    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter_ns()
        result = decide("in", "BENCH-UID", now, cache)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        if not result.allow:
            raise RuntimeError(f"Unexpected benchmark denial: {result.reason_code}")
    ordered = sorted(samples)
    p95 = ordered[round(0.95 * len(ordered)) - 1]
    print(f"samples={len(samples)} median_ms={statistics.median(samples):.3f} p95_ms={p95:.3f} max_ms={max(samples):.3f}")
    connection.close()


if __name__ == "__main__":
    main()
