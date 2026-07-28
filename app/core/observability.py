"""PostHog telemetry — the process-wide client and the backend event emitters.

Three distinct signals, deliberately kept apart because they retain differently:

- **Events** (``capture_http_request``, LLM traces) — 12 months. Anything that
  needs long history must be an event.
- **Exceptions** (``capture_exception``) — PostHog Error Tracking, stack traces.
- **Spans and logs** — exported over OTLP by ``app/core/otel.py``, ~14 days.

The client is built at lifespan startup by ``init_posthog()``. Everything here is
a no-op unless ``POSTHOG_API_KEY`` is set, and nothing may block boot or raise
into the request path — telemetry failures must never become user-visible.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Process-wide PostHog client (LLM observability). Built once at startup by
# ``init_posthog()``; None whenever capture is disabled or unavailable.
_posthog_client: object | None = None


SERVICE_NAME = "prozpr-backend"


def _service_version() -> str:
    """Short git SHA of the running build, or 'unknown'.

    Deploys are the single most common explanation for a change in any metric, so
    every event and span carries the version that produced it.
    """
    return (os.getenv("GIT_COMMIT") or "unknown")[:12]


def init_posthog() -> object | None:
    """Build the process-wide PostHog client used for LLM observability.

    Returns the client, or None when ``POSTHOG_API_KEY`` is unset or the
    ``posthog`` package is missing — in which case LLM capture is simply off and
    the app runs untouched.

    ``privacy_mode`` is set from ``POSTHOG_LLM_CAPTURE_CONTENT`` (default OFF).
    It is applied on the *client*, so it holds for every handler regardless of
    what any individual call site passes: the SDK redacts when either the client
    or the handler asks for it, so this is a floor that a call site cannot lower.
    """
    global _posthog_client
    if _posthog_client is not None:
        return _posthog_client

    from app.core.config import get_settings

    settings = get_settings()
    api_key = settings.get_posthog_api_key()
    if not api_key:
        logger.info("PostHog LLM observability disabled (POSTHOG_API_KEY not set).")
        return None

    try:
        from posthog import Posthog
    except ImportError:
        logger.warning(
            "POSTHOG_API_KEY is set but the 'posthog' package is not installed; "
            "skipping LLM observability. Run `pip install -r requirements.txt`."
        )
        return None

    capture_content = settings.posthog_llm_capture_content()
    settings_env = getattr(settings, "DEPLOY_ENV", "development")
    try:
        _posthog_client = Posthog(
            api_key,
            host=settings.get_posthog_host(),
            privacy_mode=not capture_content,
            # Stamped onto EVERY event from this process. Without these, prod and
            # staging events are indistinguishable and no event can be tied to a
            # deploy. The OTel resource in otel.py carries the same three values.
            super_properties={
                "service": SERVICE_NAME,
                "environment": settings_env,
                "service_version": _service_version(),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive; never block boot
        logger.warning("PostHog init failed (continuing without LLM capture): %s", exc)
        return None

    logger.info(
        "PostHog LLM observability enabled (host=%s, prompt/state content=%s).",
        settings.get_posthog_host(),
        "CAPTURED" if capture_content else "redacted (privacy mode)",
    )
    return _posthog_client


def get_posthog_client() -> object | None:
    """The client built by ``init_posthog()``, or None when capture is off."""
    return _posthog_client


def shutdown_posthog() -> None:
    """Flush buffered events on app shutdown. Never raises."""
    global _posthog_client
    if _posthog_client is None:
        return
    try:
        _posthog_client.shutdown()
    except Exception as exc:  # pragma: no cover - shutdown must never raise
        logger.warning("PostHog shutdown failed: %s", exc)
    finally:
        _posthog_client = None


def capture_exception(
    exc: BaseException,
    *,
    distinct_id: str | None = None,
    properties: dict[str, object] | None = None,
) -> None:
    """Report an exception to PostHog error tracking, if the client is active.

    Called by the centralised handler in ``app/core/exceptions.py``: that handler
    catches the exception and returns JSON, so nothing else can see it. Reuses the
    process-wide client built by ``init_posthog`` for LLM observability.

    No-op when PostHog is disabled (``POSTHOG_API_KEY`` unset / package missing);
    best-effort — reporting must never raise into the request path. ``distinct_id``
    ties the error to a user when known (omitted for now — the global handler has
    no user context); ``properties`` carries extra context (e.g. the request path).
    """
    client = _posthog_client
    if client is None:
        return
    kwargs: dict[str, object] = {}
    if distinct_id is not None:
        kwargs["distinct_id"] = distinct_id
    if properties is not None:
        kwargs["properties"] = properties
    try:
        client.capture_exception(exc, **kwargs)
    except Exception:  # pragma: no cover - reporting must never raise
        pass


# Paths excluded from http_request. These are load-balancer and doc polling: pure
# volume with no diagnostic value, and they would dominate a per-request event stream.
_UNTRACKED_PATHS = frozenset(
    {"/api/v1/health", "/api/v1/", "/docs", "/redoc", "/openapi.json"}
)

# Key under which the server span's start time is stashed on the ASGI scope.
_START_NS_KEY = "_prozpr_otel_start_ns"


def capture_http_request(
    *, status_code: int, path: str, method: str, duration_ms: float
) -> None:
    """Record one HTTP request as a durable PostHog *event*.

    This is the LONG-HISTORY counterpart to the OTLP span for the same request.
    Spans retain ~14 days; events retain 12 months. Error rate, endpoint latency
    trends and throughput are therefore answerable here and only here.

    ``path`` must be the route TEMPLATE. Raw paths embed user IDs in an analytics
    property and explode cardinality — 58 of 222 routes are parameterised.

    Sent with ``$process_person_profile: False``: this is an aggregate signal, and
    12 months of per-user request rows is not something you can un-send.

    No-op when PostHog is disabled; best-effort — must never raise into the request.
    """
    client = _posthog_client
    if client is None or path in _UNTRACKED_PATHS:
        return
    try:
        from app.core.exceptions import _service_from_path

        client.capture(
            "http_request",
            distinct_id="backend",
            properties={
                "status_code": status_code,
                "status_class": f"{status_code // 100}xx",
                "path": path,
                "method": method,
                "duration_ms": round(duration_ms, 2),
                "service": _service_from_path(path),
                "$process_person_profile": False,
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def otel_request_hook(span, scope) -> None:
    """OTel ``server_request_hook`` — stamp the request start onto the ASGI scope.

    Necessary because ``client_response_hook`` is handed the ``http send`` CHILD
    span, not the server span: timing from *its* ``start_time`` measures the few
    microseconds spent emitting the response, so a 150ms request would be
    recorded as ~0.1ms. The ``scope`` dict is the same object in both hooks, so
    it is the reliable carrier for the true start.
    """
    try:
        if span is not None and span.start_time:
            scope[_START_NS_KEY] = span.start_time
    except Exception:  # pragma: no cover - hooks must never raise into ASGI
        pass


def otel_response_hook(span, scope, message) -> None:
    """OTel ``client_response_hook`` — emits ``http_request`` at response start.

    Runs OUTSIDE Starlette's ServerErrorMiddleware, so it sees true 500s as well as
    the app's DB-error 503 (app/core/exceptions.py). A BaseHTTPMiddleware cannot:
    there, ``call_next`` raises on an unhandled exception and no response exists.
    """
    try:
        if message.get("type") != "http.response.start":
            return
        route = scope.get("route")
        path = getattr(route, "path", None) or "(unmatched)"
        start_ns = scope.get(_START_NS_KEY)
        duration_ms = (time.time_ns() - start_ns) / 1e6 if start_ns else 0.0
        capture_http_request(
            status_code=message.get("status", 0),
            path=path,
            method=scope.get("method", ""),
            duration_ms=duration_ms,
        )
    except Exception:  # pragma: no cover - hooks must never raise into ASGI
        pass
