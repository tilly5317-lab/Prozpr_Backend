"""OpenTelemetry provider bootstrap — exports spans and logs to PostHog over OTLP.

Two signals, one credential: PostHog's OTLP endpoints authenticate with the same
``phc_`` project token the Python SDK already uses (``POSTHOG_API_KEY``).

Retention shapes what belongs here. Spans and logs live ~14 days and are for
DEBUGGING a live incident. Anything that needs long history is emitted separately
as a PostHog *event* (12 months) through ``app.core.observability`` — see the
``http_request`` emitter. Do not "finish the migration" by moving events to spans.

Everything is a no-op when ``POSTHOG_API_KEY`` is unset, and no failure here may
prevent the app from booting.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

# The SDK default is 30_000 ms. ``TracerProvider.shutdown()`` takes no timeout of
# its own — it inherits whatever the batch processor was built with — so this is
# the only place the flush duration can be bounded. PM2 SIGKILLs the process
# ``kill_timeout`` ms after SIGINT (8_000 once Task 11 lands), and a 30s flush
# inside an 8s window is a flush that never completes.
_EXPORT_TIMEOUT_MILLIS = 5000

_tracer_provider: TracerProvider | None = None
_logger_provider: LoggerProvider | None = None


def init_otel() -> tuple[TracerProvider | None, LoggerProvider | None]:
    """Build and register the tracer and logger providers. Idempotent."""
    global _tracer_provider, _logger_provider
    if _tracer_provider is not None:
        return _tracer_provider, _logger_provider

    from app.core.config import get_settings
    from app.core.observability import SERVICE_NAME, _service_version

    settings = get_settings()
    token = (settings.get_posthog_api_key() or "").strip()
    if not token:
        logger.info("OTel disabled (POSTHOG_API_KEY not set).")
        return None, None

    # PostHog's span UI expects the STABLE HTTP semantic conventions
    # (http.request.method / http.response.status_code / url.path). Without this the
    # ASGI instrumentation emits the legacy names (http.method / http.status_code).
    # Set before any instrumentation is created — it is read at instrumentor build time.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    host = (settings.get_posthog_host() or "https://us.i.posthog.com").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": _service_version(),
            "deployment.environment": getattr(settings, "DEPLOY_ENV", "development"),
        }
    )

    try:
        _tracer_provider = TracerProvider(resource=resource)
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{host}/i/v1/traces", headers=headers),
                export_timeout_millis=_EXPORT_TIMEOUT_MILLIS,
            )
        )
        # NOTE: OTel's global provider can only be set ONCE per process. A second
        # call logs "Overriding of current TracerProvider is not allowed" and is
        # ignored, so after a shutdown/re-init cycle the global still points at the
        # dead provider while ``_tracer_provider`` points at the live one. Callers
        # must therefore pass ``get_tracer_provider()`` explicitly rather than
        # relying on ``trace.get_tracer()`` — see the instrumentation in main.py.
        trace.set_tracer_provider(_tracer_provider)

        _logger_provider = LoggerProvider(resource=resource)
        # Registration order IS execution order, so the scrubber must be added
        # before the exporter — otherwise it edits records already on their way out.
        from app.core.log_scrubber import ScrubbingLogProcessor

        _logger_provider.add_log_record_processor(ScrubbingLogProcessor())
        _logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{host}/i/v1/logs", headers=headers),
                export_timeout_millis=_EXPORT_TIMEOUT_MILLIS,
            )
        )
        set_logger_provider(_logger_provider)
    except Exception as exc:  # pragma: no cover - never block boot
        logger.warning("OTel init failed (continuing without it): %s", exc)
        _tracer_provider = None
        _logger_provider = None
        return None, None

    logger.info("OTel enabled (host=%s, traces + logs).", host)
    return _tracer_provider, _logger_provider


def attach_otel_logging(level: int = logging.WARNING) -> None:
    """Route stdlib logging to PostHog at ``level`` and above. No-op when OTel is off.

    Floors at WARNING: INFO stays on stdout/PM2 where it is free, and shipping it
    would be ~12x the log volume for records nobody queries.

    ``uvicorn`` sets ``propagate = False``, so a root handler alone misses every
    "Exception in ASGI application" traceback — it is attached explicitly.

    Attach to ``uvicorn`` but NOT ``uvicorn.error``: per uvicorn's LOGGING_CONFIG,
    ``uvicorn.error`` has no ``propagate`` override, so it bubbles up to ``uvicorn``.
    A handler on both exports every traceback TWICE, doubling volume on exactly the
    records that matter and double-counting any error-rate aggregation.

    ``uvicorn.access`` is deliberately EXCLUDED: it duplicates the http_request event
    at a fraction of the retention.
    """
    if _logger_provider is None:
        return
    # An export failure logs through the stdlib, which would re-enter this handler
    # and recurse. Keep OTel's own diagnostics on stdout only.
    logging.getLogger("opentelemetry").propagate = False
    handler = LoggingHandler(level=level, logger_provider=_logger_provider)
    for name in ("", "uvicorn"):
        logging.getLogger(name).addHandler(handler)


def get_tracer_provider() -> TracerProvider | None:
    """The provider built by ``init_otel()``, or None when OTel is off."""
    return _tracer_provider


def shutdown_otel() -> None:
    """Flush and stop both providers. Never raises.

    The flush is bounded by ``_EXPORT_TIMEOUT_MILLIS`` on the batch processors —
    see the note there for why the SDK default is unusable under PM2.
    """
    global _tracer_provider, _logger_provider
    for provider in (_tracer_provider, _logger_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()
        except Exception as exc:  # pragma: no cover - shutdown must never raise
            logger.warning("OTel provider shutdown failed: %s", exc)
    _tracer_provider = None
    _logger_provider = None
