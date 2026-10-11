"""HTTP client for the opt-in backend test server."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any

from aiohttp import ClientResponseError, ClientSession


logger = logging.getLogger(__name__)
DEFAULT_BASE_URL = "http://localhost:8766"


class TestClient:
    """Call authenticated HTTP test endpoints exposed by the backend."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        """Create an HTTP test client using an explicit or environment API key."""
        self.api_key = api_key or os.environ.get("SPS_API_KEY", "")
        if not self.api_key:
            raise ValueError("SPS_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self._session: ClientSession | None = None

    async def __aenter__(self) -> TestClient:
        """Open the underlying aiohttp session."""
        self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the underlying aiohttp session."""
        await self.close()

    def _get_session(self) -> ClientSession:
        """Return the lazily created aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session if it has been opened."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to a test endpoint and return its decoded response."""
        logger.debug("POST %s payload=%s", endpoint, payload)
        session = self._get_session()
        async with session.post(f"{self.base_url}{endpoint}", json=payload) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"Test endpoint {endpoint} returned HTTP {response.status}: {body}"
                )
            try:
                result: Any = await response.json()
            except (ValueError, ClientResponseError) as error:
                raise RuntimeError(
                    f"Test endpoint {endpoint} returned invalid JSON: {body}"
                ) from error
        if not isinstance(result, dict):
            raise RuntimeError(f"Test endpoint {endpoint} returned a non-object")
        if result.get("status") != "ok":
            raise RuntimeError(
                f"Test endpoint {endpoint} returned an error: {result!r}"
            )
        return result

    async def ping(self) -> None:
        """Check that the authenticated test server is responding."""
        await self._post("/test/ping", {})

    async def seed(self) -> dict[str, Any]:
        """Create the server's idempotent fixture data."""
        result = await self._post("/test/seed", {})
        seed_result = result.get("result")
        if not isinstance(seed_result, dict):
            raise RuntimeError("Test seed endpoint returned an invalid result")
        return seed_result

    async def set_clock(self, dt: datetime) -> None:
        """Set the backend handler clock to a fixed datetime."""
        await self._post("/test/set_clock", {"datetime": dt.isoformat()})

    async def reset_clock(self) -> None:
        """Clear the backend handler clock override."""
        await self._post("/test/reset_clock", {})

    async def refresh_cache(self) -> None:
        """Ask the backend to reload its in-memory cache."""
        await self._post("/test/refresh_cache", {})

    async def lock(self, uid: str) -> None:
        """Temporarily lock a credential by UID."""
        await self._post("/test/lock", {"uid": uid})

    async def unlock(self, uid: str) -> None:
        """Unlock a credential by UID."""
        await self._post("/test/unlock", {"uid": uid})

    async def mark_lost(self, uid: str) -> None:
        """Mark a credential lost by UID."""
        await self._post("/test/mark_lost", {"uid": uid})

    async def set_in_lot(self, plate: str, value: bool) -> None:
        """Set a vehicle's in-lot flag."""
        await self._post("/test/set_in_lot", {"plate": plate, "value": value})

    async def set_plan_expiry(self, plate: str, when: date | None) -> None:
        """Set or clear a vehicle's plan expiry."""
        await self._post(
            "/test/set_plan_expiry",
            {"plate": plate, "date": when.isoformat() if when is not None else None},
        )
