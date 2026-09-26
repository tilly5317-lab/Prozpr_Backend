"""`GET /health` must report DB failure via the HTTP status code, not just the body.

Bug being guarded: ``health_check`` returned HTTP 200 even when the database was
unreachable (the "degraded" signal lived only in the JSON body). A status-code-based
uptime monitor therefore could not detect a database outage. When the DB check fails
the endpoint must return HTTP 503 so an external monitor alarms.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.responses import JSONResponse


async def test_health_returns_503_when_db_unhealthy() -> None:
    from app.routers.health import health_check

    failing_db = AsyncMock()
    failing_db.execute = AsyncMock(side_effect=Exception("connection lost"))

    result = await health_check(db=failing_db)

    assert isinstance(result, JSONResponse), (
        "an unhealthy DB must return a JSONResponse so the HTTP status can be 503"
    )
    assert result.status_code == 503


async def test_health_returns_200_when_db_healthy() -> None:
    from app.routers.health import health_check

    ok_db = AsyncMock()
    ok_db.execute = AsyncMock(return_value=None)

    result = await health_check(db=ok_db)

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["database"] == "healthy"
