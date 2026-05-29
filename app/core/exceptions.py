"""Centralised FastAPI exception handlers.

One entry point — ``register_exception_handlers(app)`` — attaches every handler
to the FastAPI instance. ``app.main`` calls it once after the app is built.

Handlers translate a small set of known error families into stable JSON:

    ValidationError      → 422 Unprocessable Entity
    DB auth failed       → 503 Service Unavailable  (clear hint)
    DB host unreachable  → 503 Service Unavailable  (clear hint)
    DB connection closed → 503 Service Unavailable  (clear hint)
    anything else        → 500 Internal Server Error (opaque)

DB-error classification walks the ``__cause__`` chain so a wrapped SQLAlchemy
error still gets recognised.
"""

from __future__ import annotations

import logging
from typing import Iterator

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)


# (substrings-to-match, response-message). Order matters only for which message
# wins when multiple patterns match — the first hit returns.
_DB_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("password authentication failed", "invalidpassworderror"),
        "Database connection failed: authentication error. Check DATABASE_URL.",
    ),
    (
        ("getaddrinfo failed", "name or service not known"),
        "Database connection failed: host unreachable. Check DATABASE_URL.",
    ),
    (
        ("connection was closed",),
        "Database connection was closed. Retry the request.",
    ),
)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every exception handler to ``app``. Call once from ``main``."""

    @app.exception_handler(ValidationError)
    async def _on_validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        logger.error("Validation error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(_: Request, exc: Exception) -> JSONResponse:
        db_message = _classify_db_error(exc)
        if db_message is not None:
            logger.error("Database error: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": db_message},
            )
        logger.error("Unexpected error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred"},
        )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _classify_db_error(exc: BaseException) -> str | None:
    """Return the user-facing message for a known DB failure, else ``None``."""
    for cur in _walk_causes(exc):
        msg = str(cur).lower()
        for keywords, response in _DB_ERROR_PATTERNS:
            if any(kw in msg for kw in keywords):
                return response
    return None


def _walk_causes(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` then each ``__cause__`` in turn. Guards against cycles."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        yield cur
        seen.add(id(cur))
        cur = cur.__cause__
