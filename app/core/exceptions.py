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
from starlette.requests import ClientDisconnect

from app.core.observability import capture_exception

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
        # Log field NAMES only. ``str(exc)`` embeds the submitted values, which for
        # this app means holdings, amounts and personal details — and logs are
        # exported off-platform. The response body still carries full detail to the
        # caller, who already owns that data.
        fields = ", ".join(
            ".".join(str(p) for p in err.get("loc", ())) for err in exc.errors()
        )
        logger.error("Validation error on %d field(s): %s", exc.error_count(), fields)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ClientDisconnect)
    async def _on_client_disconnect(_: Request, __: ClientDisconnect) -> JSONResponse:
        # The caller hung up mid-upload — their network, not our bug. Without this it
        # falls through to the catch-all and becomes a $exception plus a phantom 5xx
        # in the 12-month event stream, which would page on-call for flaky mobile
        # connections. 499 is nginx's non-standard "client closed request".
        logger.info("Client disconnected before the request completed.")
        return JSONResponse(status_code=499, content={"detail": "Client disconnected"})

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Report to PostHog before we swallow the exception into JSON — automatic
        # capture can't see an exception that a handler catches.
        # request.state (NOT a ContextVar): BaseHTTPMiddleware runs the downstream
        # app in its own task, so a ContextVar set in a handler is invisible here.
        # scope["state"] crosses that boundary. Do not "simplify" this.
        distinct_id = getattr(request.state, "distinct_id", None)
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        capture_exception(
            exc,
            distinct_id=distinct_id,
            properties={
                "path": path,
                "error_type": type(exc).__name__,
                "service": _service_from_path(path),
                # No user known -> don't mint a throwaway person profile per error.
                **({} if distinct_id else {"$process_person_profile": False}),
            },
        )
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


def _service_from_path(path: str) -> str:
    """Best-effort service/domain name from a request path.

    ``/api/v1/mf-ingest/cams-pdf`` -> ``mf-ingest`` — used to group 5xx errors by
    the domain that raised them, so the PostHog dashboard can answer "which
    service is erroring" without opening every stack trace.
    """
    parts = [p for p in path.split("/") if p]
    if "v1" in parts:
        i = parts.index("v1")
        if i + 1 < len(parts):
            return parts[i + 1]
    return parts[0] if parts else "unknown"


def _walk_causes(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` then each ``__cause__`` in turn. Guards against cycles."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        yield cur
        seen.add(id(cur))
        cur = cur.__cause__
