"""Opt-in HTTP endpoints for integration-test setup and control."""

import asyncio
import hmac
import logging
import os
import sqlite3
from datetime import date, datetime
from typing import Any, Callable

from aiohttp import web

from backend import api_core, handlers
from backend.cache import CacheState


logger = logging.getLogger(__name__)


def _authorized(request: web.Request, api_key: str) -> bool:
    """Check the bearer token using a constant-time comparison."""
    authorization = request.headers.get("Authorization", "")
    prefix = "Bearer "
    return authorization.startswith(prefix) and hmac.compare_digest(
        authorization[len(prefix) :], api_key
    )


@web.middleware
async def _auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Any],
) -> web.StreamResponse:
    """Require API-key authorization for every test endpoint."""
    api_key = str(request.app["api_key"])
    if not _authorized(request, api_key):
        return web.json_response(
            {"status": "error", "message": "Unauthorized"}, status=401
        )
    return await handler(request)


async def _json_body(request: web.Request) -> dict[str, Any]:
    """Read a JSON object request body or raise ValueError."""
    try:
        value = await request.json()
    except (web.HTTPException, ValueError) as error:
        raise ValueError("Request body must be a JSON object") from error
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")
    return value


async def _run_operation(
    request: web.Request,
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> web.Response:
    """Run a synchronous operation under the shared DB lock in a worker."""
    cache: CacheState = request.app["cache"]

    def locked_operation() -> Any:
        with cache.db_lock:
            return operation(*args, **kwargs)

    try:
        result = await asyncio.to_thread(locked_operation)
    except ValueError as error:
        return web.json_response(
            {"status": "error", "message": str(error)}, status=400
        )
    except Exception:
        logger.exception("Test HTTP operation failed: %s", request.path)
        return web.json_response(
            {"status": "error", "message": "Internal server error"}, status=500
        )
    return web.json_response({"status": "ok", "result": result})


def _required_string(payload: dict[str, Any], name: str) -> str:
    """Return a non-empty string request value."""
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _seed_database(conn: sqlite3.Connection, cache: CacheState) -> dict[str, int]:
    """Seed a stable set of vehicles and credentials through the core API."""
    unit_id = api_core.create_unit(conn, cache, "TEST-UNIT")
    created: dict[str, int] = {"unit_id": unit_id}

    for plate, vehicle_type, uid in (
        ("CAR-1", "car", "UID-CAR"),
        ("MOTO-1", "moto", "UID-MOTO"),
    ):
        vehicle_id = api_core.create_vehicle(
            conn,
            cache,
            unit_id=unit_id,
            plate=plate,
            vehicle_type=vehicle_type,
        )
        credential_id = api_core.create_credential(
            conn, cache, vehicle_id=vehicle_id, uid=uid
        )
        created[f"{plate}_vehicle_id"] = vehicle_id
        created[f"{plate}_credential_id"] = credential_id

    for index in range(19):
        plate = f"SIM-FULL-CAR-{index}"
        vehicle_id = api_core.create_vehicle(
            conn,
            cache,
            unit_id=unit_id,
            plate=plate,
            vehicle_type="car",
            plan_expires_at=date(2099, 12, 31),
        )
        credential_id = api_core.create_credential(
            conn,
            cache,
            vehicle_id=vehicle_id,
            uid=f"UID-SIM-FULL-CAR-{index}",
        )
        created[f"{plate}_vehicle_id"] = vehicle_id
        created[f"{plate}_credential_id"] = credential_id
    return created


def _make_app(
    conn: sqlite3.Connection, cache: CacheState, api_key: str
) -> web.Application:
    """Create the authenticated test-only aiohttp application."""
    app = web.Application(middlewares=[_auth_middleware])
    app["api_key"] = api_key
    app["connection"] = conn
    app["cache"] = cache

    async def ping(request: web.Request) -> web.Response:
        """Return a simple authenticated health response."""
        try:
            await _json_body(request)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return web.json_response({"status": "ok"})

    async def refresh_cache(request: web.Request) -> web.Response:
        """Reload the in-memory cache from SQLite."""
        try:
            await _json_body(request)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(request, cache.refresh_from_db)

    async def set_clock(request: web.Request) -> web.Response:
        """Set the handler clock override to a supplied ISO datetime."""
        try:
            payload = await _json_body(request)
            value = _required_string(payload, "datetime")
            clock_value = datetime.fromisoformat(value)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(request, handlers.set_clock_override, clock_value)

    async def reset_clock(request: web.Request) -> web.Response:
        """Clear the handler clock override."""
        try:
            await _json_body(request)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(request, handlers.set_clock_override, None)

    async def seed(request: web.Request) -> web.Response:
        """Create the idempotent set of simulator test records."""
        try:
            await _json_body(request)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(
            request, _seed_database, request.app["connection"], cache
        )

    async def change_credential(
        request: web.Request, operation: Callable[..., None]
    ) -> web.Response:
        """Apply a credential status operation by UID."""
        try:
            payload = await _json_body(request)
            uid = _required_string(payload, "uid")
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(
            request, operation, request.app["connection"], cache, uid
        )

    async def lock(request: web.Request) -> web.Response:
        """Lock a credential through the core API."""
        return await change_credential(request, api_core.lock_credential)

    async def unlock(request: web.Request) -> web.Response:
        """Unlock a credential through the core API."""
        return await change_credential(request, api_core.unlock_credential)

    async def mark_lost(request: web.Request) -> web.Response:
        """Mark a credential lost through the core API."""
        return await change_credential(request, api_core.mark_credential_lost)

    async def set_in_lot(request: web.Request) -> web.Response:
        """Set vehicle occupancy through the core API."""
        try:
            payload = await _json_body(request)
            plate = _required_string(payload, "plate")
            value = payload.get("value")
            if not isinstance(value, bool):
                raise ValueError("value must be a boolean")
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(
            request,
            api_core.set_vehicle_in_lot,
            request.app["connection"],
            cache,
            plate,
            value,
            actor="test_server",
        )

    async def reset_occupancy(request: web.Request) -> web.Response:
        """Put all vehicles outside the lot for the next hardware test."""
        try:
            await _json_body(request)
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(
            request,
            api_core.reset_all_occupancy,
            request.app["connection"],
            cache,
        )

    async def set_plan_expiry(request: web.Request) -> web.Response:
        """Set or clear a vehicle plan expiration through the core API."""
        try:
            payload = await _json_body(request)
            plate = _required_string(payload, "plate")
            date_value = payload.get("date")
            if date_value is not None and not isinstance(date_value, str):
                raise ValueError("date must be an ISO date string or null")
            expiry = date.fromisoformat(date_value) if date_value is not None else None
        except ValueError as error:
            return web.json_response(
                {"status": "error", "message": str(error)}, status=400
            )
        return await _run_operation(
            request,
            api_core.set_vehicle_plan_expiry,
            request.app["connection"],
            cache,
            plate,
            expiry,
        )

    app.add_routes(
        [
            web.post("/test/ping", ping),
            web.post("/test/refresh_cache", refresh_cache),
            web.post("/test/set_clock", set_clock),
            web.post("/test/reset_clock", reset_clock),
            web.post("/test/seed", seed),
            web.post("/test/lock", lock),
            web.post("/test/unlock", unlock),
            web.post("/test/mark_lost", mark_lost),
            web.post("/test/set_in_lot", set_in_lot),
            web.post("/test/reset_occupancy", reset_occupancy),
            web.post("/test/set_plan_expiry", set_plan_expiry),
        ]
    )
    return app


async def start_test_server(
    conn: sqlite3.Connection,
    cache: CacheState,
    api_key: str,
) -> web.AppRunner | None:
    """Start the test HTTP server when SPS_TEST_MODE is enabled."""
    if os.environ.get("SPS_TEST_MODE") != "1":
        logger.info("test server disabled")
        return None

    runner = web.AppRunner(_make_app(conn, cache, api_key))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8766)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    logger.info("Test server listening on 0.0.0.0:8766")
    return runner
