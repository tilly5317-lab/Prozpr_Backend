"""PostHog telemetry — the process-wide client and the backend event emitters.

Events retain 12 months; the OTLP spans and logs in ``otel.py`` retain ~14 days.
That split is why anything needing long history is emitted here as an event.

No-op unless ``POSTHOG_API_KEY`` is set. Nothing here may block boot or raise
into a request — telemetry failures must never become user-visible.
"""

from __future__ import annotations

import logging
import os
import time

from app.core.pii import redact_obj, redact_text

logger = logging.getLogger(__name__)

_posthog_client: object | None = None


SERVICE_NAME = "prozpr-backend"


def _service_version() -> str:
    """Short git SHA of the running build, or 'unknown'."""
    return (os.getenv("GIT_COMMIT") or "unknown")[:12]


def init_posthog() -> object | None:
    """Build the process-wide PostHog client. None when capture is unavailable.

    ``privacy_mode`` is set on the CLIENT, not per handler: the SDK redacts when
    either asks for it, making this a floor no call site can lower.
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


# Stamped on an exception once it has been filed. The 500 handler, the job
# reporter, and the logging handler can all see the same exception on its way
# out; each SHOULD still mark its own span, but the issue files exactly once.
_REPORTED_FLAG = "_prozpr_failure_reported"


def _redact_exception_args(exc: BaseException) -> None:
    """Scrub identifier-shaped text out of an exception's args, in place.

    In place rather than on a copy because PostHog serialises the exception
    asynchronously — a restored-afterwards message would be a race. The local
    log line has already been written by the time we get here, so nothing
    diagnostic is lost from stdout.
    """
    try:
        args = getattr(exc, "args", None)
        if not args:
            return
        redacted = tuple(
            redact_text(a) if isinstance(a, str) else a for a in args
        )
        if redacted != args:
            exc.args = redacted
    except Exception:  # pragma: no cover - never break reporting
        pass


def capture_exception(
    exc: BaseException,
    *,
    distinct_id: str | None = None,
    properties: dict[str, object] | None = None,
) -> None:
    """Report an exception to PostHog Error Tracking. Files each exception once.

    Three callers can see the same exception on its way out — the 500 handler,
    the job reporter, and the logging handler in ``error_capture.py``. Each should
    still mark its own span; the dedupe below is what keeps the issue count honest.
    """
    client = _posthog_client
    if client is None or getattr(exc, _REPORTED_FLAG, False):
        return

    # Error Tracking retains 12 months — four times the OTLP log window — and it
    # is a SEPARATE pipeline, so `ScrubbingLogProcessor` never sees anything sent
    # here. Third-party clients raise with the upstream response embedded in the
    # message, which is how PAN, date of birth, bank account and IFSC reached a
    # year-long store. Sources are fixed too (see FpError); this is the backstop
    # for the next client nobody remembered to fix.
    _redact_exception_args(exc)

    kwargs: dict[str, object] = {}
    if distinct_id is not None:
        kwargs["distinct_id"] = distinct_id
    if properties is not None:
        kwargs["properties"] = redact_obj(properties)
    try:
        client.capture_exception(exc, **kwargs)
        try:
            setattr(exc, _REPORTED_FLAG, True)
        except AttributeError:  # pragma: no cover - exotic exceptions
            pass
    except Exception:  # pragma: no cover - reporting must never raise
        pass


# Load-balancer and doc polling: pure volume, no diagnostic value.
_UNTRACKED_PATHS = frozenset(
    {"/api/v1/health", "/api/v1/", "/docs", "/redoc", "/openapi.json"}
)

# Requests that matched no route. Scanner traffic on the public IP was 93% of all
# events, enough to read the aggregate 4xx rate as ~93% when the real rate on
# served routes is under 1%. Excluded on the unmatched ROUTE, not the 404 status —
# a handler answering "no such goal" is real signal and is kept.
_UNMATCHED_PATH = "(unmatched)"

_START_NS_KEY = "_prozpr_otel_start_ns"


def capture_http_request(
    *, status_code: int, path: str, method: str, duration_ms: float
) -> None:
    """Record one HTTP request as a durable PostHog *event*.

    The long-history counterpart to the request's OTLP span: error rate, latency
    trends and throughput are answerable here and only here.

    ``path`` must be the route TEMPLATE — 58 of 222 routes are parameterised, and
    raw paths would put user IDs in an analytics property.
    """
    client = _posthog_client
    if client is None or path in _UNTRACKED_PATHS or path == _UNMATCHED_PATH:
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
                # NOT "service" — that name is taken by a super property, which
                # merges second and would silently overwrite this
                # (posthog/client.py:1666). "service" is the deployable,
                # "domain" is the business area.
                "domain": _service_from_path(path),
                "$process_person_profile": False,
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def capture_flow_completed(
    *,
    intent: str | None,
    outcome: str,
    failure_reason: str | None,
    duration_ms: float,
    distinct_id: object | None,
) -> None:
    """Record the outcome of one chat turn as a durable event.

    Every AI flow enters through one endpoint and returns 201 whether it produced
    a plan or an apology, so no HTTP status can separate them. This can.

    ``failure_reason`` is an exception CLASS name or a fixed token — never
    ``str(exc)``, which would put user data in a 12-month property.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "flow_completed",
            distinct_id=str(distinct_id) if distinct_id else "backend",
            properties={
                "intent": intent,
                "outcome": outcome,
                "failure_reason": failure_reason,
                "duration_ms": round(duration_ms, 2),
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def capture_preference_unserved(
    *,
    flow: str,
    failure_class: str,
    session_id: object | None,
    distinct_id: object | None,
) -> None:
    """A customer investment preference we could not serve (spec 2026-08-24).

    ``failure_class`` is a fixed token, never free text. NO chat content —
    PostHog holds no transcripts; join to the Postgres transcript rows by
    session_id + event timestamp to read the actual ask. This event is the
    demand signal that grows the preference vocabulary.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "preference_unserved",
            distinct_id=str(distinct_id) if distinct_id else "backend",
            properties={
                "flow": flow,
                "failure_class": failure_class,
                "session_id": str(session_id) if session_id else None,
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def capture_job_completed(
    *,
    job: str,
    outcome: str,
    failure_reason: str | None,
    duration_ms: float,
    counts: dict[str, object] | None = None,
) -> None:
    """Record one background job run as a durable event.

    An event, not just a span: a job that stops running entirely emits no error,
    no span and no log, so it is detectable only as the ABSENCE of an expected
    event — and absence can only be charted against a durable signal.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "job_completed",
            distinct_id="backend",
            properties={
                "job": job,
                "outcome": outcome,
                "failure_reason": failure_reason,
                "duration_ms": round(duration_ms, 2),
                "$process_person_profile": False,
                **(counts or {}),
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def otel_request_hook(span, scope) -> None:
    """OTel ``server_request_hook`` — stamp the request start onto the ASGI scope.

    ``client_response_hook`` receives the ``http send`` CHILD span, not the server
    span, so timing off it reports ~0.1ms for a 150ms request. The ``scope`` dict
    is the same object in both hooks and is the reliable carrier.
    """
    try:
        if span is not None and span.start_time:
            scope[_START_NS_KEY] = span.start_time
    except Exception:  # pragma: no cover - hooks must never raise into ASGI
        pass


def otel_response_hook(span, scope, message) -> None:
    """OTel ``client_response_hook`` — emits ``http_request`` at response start.

    Runs OUTSIDE Starlette's ServerErrorMiddleware, so it sees true 500s. A
    BaseHTTPMiddleware cannot: there ``call_next`` raises and no response exists.
    """
    try:
        if message.get("type") != "http.response.start":
            return
        route = scope.get("route")
        path = getattr(route, "path", None) or _UNMATCHED_PATH
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
