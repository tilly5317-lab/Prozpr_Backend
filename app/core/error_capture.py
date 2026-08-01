"""Forward logged exceptions to PostHog Error Tracking.

74 call sites log an exception with a full stack trace. Before this, 2 of them
reached Error Tracking; the rest produced a log line that expires in ~14 days
and cannot be counted, charted, or grouped. One handler covers all of them —
and every ``except Exception: logger.exception(...)`` written from here on.
"""

from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Per-site rate limit, keyed by (logger, exception type, file, line).
# networth_history_service.py:367 logs inside a per-scheme loop, so one upstream
# outage would otherwise file thousands of issues from a single code line. The
# log line still ships either way — this only drops duplicate issue reports.
_THROTTLE_SECONDS = 60.0
_last_sent: dict[tuple[str, str, str, int], float] = {}

# Stops a failure inside PostHog's own SDK from logging its way back in here.
_in_handler: ContextVar[bool] = ContextVar("prozpr_error_capture_active", default=False)


class ErrorTrackingHandler(logging.Handler):
    """Files an Error Tracking issue for any log record carrying ``exc_info``."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not record.exc_info:
                return
            exc = record.exc_info[1]
            if exc is None or _in_handler.get():
                return

            key = (record.name, type(exc).__name__, record.pathname, record.lineno)
            now = time.monotonic()
            last = _last_sent.get(key)
            if last is not None and now - last < _THROTTLE_SECONDS:
                return
            _last_sent[key] = now

            token = _in_handler.set(True)
            try:
                from app.core.observability import capture_exception

                capture_exception(exc, properties={"logger": record.name})
            finally:
                _in_handler.reset(token)
        except Exception:  # pragma: no cover - a logging handler must never raise
            pass


def attach_error_capture(level: int = logging.WARNING) -> None:
    """Attach the handler to the root and uvicorn loggers.

    WARNING rather than ERROR: the stuck-advisory-lock failure at
    ``mfapi_scheduler.py:214`` is logged at WARNING with ``exc_info=True``, and
    an ERROR-only handler would miss it. The throttle is what makes the wider
    level affordable.

    Same targets as ``attach_otel_logging`` — root and ``uvicorn``, NOT
    ``uvicorn.error``, which propagates and would file everything twice.
    """
    enabled = os.getenv("POSTHOG_ERROR_CAPTURE_ENABLED", "true").strip().lower()
    if enabled not in ("1", "true", "yes"):
        logger.info("PostHog error capture disabled by POSTHOG_ERROR_CAPTURE_ENABLED.")
        return
    handler = ErrorTrackingHandler(level=level)
    for name in ("", "uvicorn"):
        logging.getLogger(name).addHandler(handler)
    logger.info("PostHog error capture attached at %s.", logging.getLevelName(level))
