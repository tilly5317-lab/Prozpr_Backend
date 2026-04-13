"""Health and lightweight deploy metadata (for verifying what revision is live)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {"message": "Ask Tilly API", "version": "2.0.0"}


@router.get("/deploy-info")
async def deploy_info():
    """Return API version and optional git SHA from build-time env (set in Docker/CI)."""
    settings = get_settings()
    sha = (
        (os.getenv("GIT_COMMIT") or os.getenv("RENDER_GIT_COMMIT") or os.getenv("VERCEL_GIT_COMMIT_SHA") or "")
        .strip()
    )
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

    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status,
    }
