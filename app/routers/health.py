"""Health and lightweight deploy metadata (for verifying what revision is live)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {"message": "Ask PI API", "version": "2.0.0"}


@router.get("/deploy-info")
async def deploy_info():
    """Return API version and optional git SHA from build-time env (set in Docker/CI)."""
    settings = get_settings()
    sha = (
        os.getenv("GIT_COMMIT")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("VERCEL_GIT_COMMIT_SHA")
        or ""
    ).strip()
    return {
        "api_version": settings.VERSION,
        "git_commit": sha or None,
        "project": settings.PROJECT_NAME,
    }


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "healthy"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    parsed = make_url(get_settings().get_database_url())
    if parsed.drivername.startswith("postgresql"):
        backend = "postgresql"
    elif "sqlite" in parsed.drivername:
        backend = "sqlite"
    else:
        backend = parsed.drivername

    payload = {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status,
        "database_backend": backend,
    }
    # Report unhealthy via the HTTP status code (503), not just the body, so a
    # status-code-based uptime monitor actually alarms on a DB outage.
    if db_status != "healthy":
        return JSONResponse(content=payload, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return payload
