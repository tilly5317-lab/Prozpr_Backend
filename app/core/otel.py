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
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
)

logger = logging.getLogger(__name__)

# The SDK default is 30_000 ms. ``TracerProvider.shutdown()`` takes no timeout of
# its own — it inherits whatever the batch processor was built with — so this is
# the only place the flush duration can be bounded. PM2 SIGKILLs the process
# ``kill_timeout`` ms after SIGINT (8_000 once Task 11 lands), and a 30s flush
# inside an 8s window is a flush that never completes.
_EXPORT_TIMEOUT_MILLIS = 5000

_tracer_provider: TracerProvider | None = None
_logger_provider: LoggerProvider | None = None
_meter_provider: object | None = None

# How often infra gauges are shipped. 60s matches the resolution anyone actually
# looks at and keeps the series count — not the sample count — the thing that
# matters for cost.
_METRIC_EXPORT_INTERVAL_MILLIS = 60_000

# Deliberately NOT the SystemMetricsInstrumentor default. Its default config
# includes per-CPU-core, per-disk and per-NIC breakdowns, and a series is one
# unique attribute combination — that config turns 4 questions into hundreds of
# series. These five answer "is the box healthy".
_SYSTEM_METRICS = {
    "system.cpu.utilization": ["idle", "user", "system", "iowait"],
    "system.memory.usage": ["used", "free", "cached"],
    "system.memory.utilization": ["used", "free", "cached"],
    "system.disk.usage": ["used", "free"],
    "process.runtime.memory": ["rss", "vms"],
}

# Where the ASGI layer puts the request path. ``url.path`` is the stable HTTP
# semconv name selected by the opt-in below; ``http.target`` is the legacy name,
# kept as a fallback so the filter still works if that opt-in ever goes away.
_PATH_ATTRIBUTES = ("url.path", "http.target")


class _ServedPathSampler(Sampler):
    """Drop spans for requests to paths this API does not serve.

    The box answers on a public IP, so most of what reaches it is internet
    background noise: BitTorrent tracker probes (``/announce``, ``/scrape``),
    ``.env`` hunting, PHP shell scans. That was 93% of span volume — paid for,
    retained, and diagnostically worthless.

    The matched ROUTE would be the ideal signal, but it is not known until the
    request is dispatched and sampling is decided at span START. The PATH is
    known then, and everything this app serves lives under ``API_V1_PREFIX``, so
    the prefix is the discriminator.

    Only spans that CARRY a path are candidates. Internal spans — schedulers,
    LLM chains — have no path attribute and are always sampled.

    Wrapped in ``ParentBased`` by the caller: the ``http send`` children end
    before their server span does, so they cannot be judged on their own; an
    unsampled parent has to take its whole subtree with it.
    """

    def __init__(self, served_prefix: str) -> None:
        self._served_prefix = served_prefix

    def should_sample(
        self,
        parent_context,
        trace_id,
        name,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:
        path = None
        for key in _PATH_ATTRIBUTES:
            value = (attributes or {}).get(key)
            if value:
                path = str(value)
                break
        if path is not None and not path.startswith(self._served_prefix):
            return SamplingResult(Decision.DROP, attributes, trace_state)
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)

    def get_description(self) -> str:
        return f"ServedPathSampler({self._served_prefix})"


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
        _tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(
                root=_ServedPathSampler(settings.API_V1_PREFIX),
            ),
        )
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


def init_metrics() -> object | None:
    """Start CPU / memory / disk gauges, exported to PostHog over OTLP. Idempotent.

    The third OTLP signal, alongside the tracer and logger providers built by
    ``init_otel()``. Answers the one question spans and logs cannot: was the BOX
    healthy. Nothing else in PostHog can tell you the process was at its memory
    ceiling when a job died.

    Same no-op contract as the rest of this module: off without
    ``POSTHOG_API_KEY``, and never allowed to block boot.
    """
    global _meter_provider
    if _meter_provider is not None:
        return _meter_provider

    from app.core.config import get_settings
    from app.core.observability import SERVICE_NAME, _service_version

    settings = get_settings()
    token = (settings.get_posthog_api_key() or "").strip()
    if not token:
        logger.info("OTel metrics disabled (POSTHOG_API_KEY not set).")
        return None

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.instrumentation.system_metrics import (
            SystemMetricsInstrumentor,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        host = (settings.get_posthog_host() or "https://us.i.posthog.com").rstrip("/")
        resource = Resource.create(
            {
                "service.name": SERVICE_NAME,
                "service.version": _service_version(),
                "deployment.environment": getattr(
                    settings, "DEPLOY_ENV", "development"
                ),
            }
        )
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=f"{host}/i/v1/metrics",
                headers={"Authorization": f"Bearer {token}"},
            ),
            export_interval_millis=_METRIC_EXPORT_INTERVAL_MILLIS,
        )
        _meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        # Not set as the OTel global, for the same reason the tracer provider is
        # passed explicitly everywhere: the global can only be set once per
        # process, so a re-init leaves it pointing at a dead provider.
        SystemMetricsInstrumentor(config=_SYSTEM_METRICS).instrument(
            meter_provider=_meter_provider
        )
    except Exception as exc:  # pragma: no cover - never block boot
        logger.warning("OTel metrics init failed (continuing without it): %s", exc)
        _meter_provider = None
        return None

    logger.info("OTel metrics enabled (host=%s, %d gauges).", host, len(_SYSTEM_METRICS))
    return _meter_provider


def get_meter_provider() -> object | None:
    """The provider built by ``init_metrics()``, or None when metrics are off."""
    return _meter_provider


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
    global _tracer_provider, _logger_provider, _meter_provider
    for provider in (_tracer_provider, _logger_provider, _meter_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - shutdown must never raise
            logger.warning("OTel provider shutdown failed: %s", exc)
    _tracer_provider = None
    _logger_provider = None
    _meter_provider = None
