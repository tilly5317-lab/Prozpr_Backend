"""Health and lightweight deploy metadata (for verifying what revision is live)."""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
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
    # Whether chat answers can stream. The reply is a forced tool call, so it
    # only arrives incrementally with the fine-grained-tool-streaming beta, which
    # the formatter passes as ChatAnthropic(betas=[...]) — a real field only from
    # langchain-anthropic 1.4.x. Older releases swallow the kwarg and the answer
    # lands in one lump with no error raised anywhere, so this is otherwise
    # invisible without shell access to the host. False here means the running
    # venv is behind requirements.txt.
    try:
        from langchain_anthropic import ChatAnthropic

        tool_streaming = "betas" in ChatAnthropic.model_fields
    except Exception:
        tool_streaming = None

    return {
        "api_version": settings.VERSION,
        "git_commit": sha or None,
        "project": settings.PROJECT_NAME,
        "tool_streaming": tool_streaming,
    }


@router.get("/stream-check")
async def stream_check():
    """Emit 20 SSE ticks, 250ms apart, so a caller can prove whether anything
    between uvicorn and the client is buffering.

    Unauthenticated on purpose: chat streams need a login, which makes the one
    question that matters — do frames arrive spread out, or all at once at the
    end? — untestable from outside the host. Each tick carries the server's own
    elapsed milliseconds, so comparing them against arrival times separates a
    slow server from a buffering proxy. Carries no data of any kind. DIAGNOSTIC
    ONLY — delete once production streaming is confirmed.
    """

    async def ticks():
        # Same prelude the chat stream uses, for the same reason: overflow a
        # default proxy buffer so the first flush cannot be held back.
        yield ":" + (" " * 8192) + "\n\n"
        start = time.monotonic()
        for i in range(20):
            ms = int((time.monotonic() - start) * 1000)
            yield f"event: tick\ndata: {{\"i\": {i}, \"server_ms\": {ms}}}\n\n"
            await asyncio.sleep(0.25)
        yield 'event: done\ndata: {"ok": true}\n\n'

    return StreamingResponse(
        ticks(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
