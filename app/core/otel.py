"""OpenTelemetry provider bootstrap — spans, logs and metrics to PostHog over OTLP.

All three endpoints authenticate with the same ``phc_`` project token
(``POSTHOG_API_KEY``). Everything here is a no-op without it, and no failure may
block boot.

Spans and logs retain ~14 days; anything needing long history is a PostHog event
instead (``app.core.observability``). Don't move events to spans.
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

# The SDK default is 30_000 ms, and shutdown() inherits whatever the batch
# processor was built with — so this is the only place the flush can be bounded.
# PM2 SIGKILLs 8_000 ms after SIGINT (ecosystem.config.cjs), and a 30s flush
# inside an 8s window never completes.
_EXPORT_TIMEOUT_MILLIS = 5000

_tracer_provider: TracerProvider | None = None
_logger_provider: LoggerProvider | None = None
_meter_provider: object | None = None

_METRIC_EXPORT_INTERVAL_MILLIS = 60_000

# A deliberate subset — the instrumentor's default adds swap, per-NIC network and
# three disk-IO metrics, turning "is the box healthy" into ~100 billable series.
#
# An unsupported key is SILENTLY IGNORED: a "system.disk.usage" entry here bought
# no disk metric at all until the preflight caught it. test_metrics_bootstrap now
# pins every key against the instrumentor's own table.
#
# system.cpu.utilization is unavoidably PER CORE (percpu=True + a `cpu` label), so
# its series count is cores x states. iowait is Linux-only.
_SYSTEM_METRICS = {
    "system.cpu.utilization": ["idle", "user", "system", "iowait"],
    "system.memory.usage": ["used", "free", "cached"],
    "system.memory.utilization": ["used", "free", "cached"],
    # Replaces the deprecated process.runtime.* keys.
    "process.memory.usage": None,
    "process.memory.virtual": None,
}

# The instrumentor's disk metrics are all throughput; free space has no entry at
# all, so it gets the hand-rolled gauge below.
_DISK_PATH = "/"

# ``url.path`` is the stable semconv name selected by the opt-in in init_otel;
# ``http.target`` is the legacy fallback.
_PATH_ATTRIBUTES = ("url.path", "http.target")


class _ServedPathSampler(Sampler):
    """Drop spans for paths this API does not serve — scanner noise was 93% of volume.

    Matches on PATH, not the matched route: sampling is decided at span START,
    before dispatch, so the route is not known yet.

    Spans with no path attribute (schedulers, LLM chains) are always sampled.
    Must be wrapped in ``ParentBased`` — the ``http send`` children end before
    their server span and cannot be judged alone.
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

    # PostHog's span UI expects the STABLE HTTP semconv names. Must be set before
    # any instrumentation is built — it is read at instrumentor construction.
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
        # The OTel global can only be set ONCE per process, so after a
        # shutdown/re-init it still points at the dead provider. Callers must pass
        # get_tracer_provider() explicitly, never rely on trace.get_tracer().
        trace.set_tracer_provider(_tracer_provider)

        _logger_provider = LoggerProvider(resource=resource)
        # Registration order is execution order: the scrubber must precede the
        # exporter or it edits records already on their way out.
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


def _add_disk_free_gauge(meter_provider: object) -> None:
    """Register ``system.filesystem.utilization`` — fraction of ``_DISK_PATH`` used."""
    from opentelemetry.metrics import CallbackOptions, Observation

    def observe(_options: CallbackOptions):
        import psutil

        try:
            usage = psutil.disk_usage(_DISK_PATH)
        except OSError:  # pragma: no cover - path vanished / permission
            return
        yield Observation(usage.percent / 100.0, {"path": _DISK_PATH})

    meter_provider.get_meter(__name__).create_observable_gauge(  # type: ignore[attr-defined]
        name="system.filesystem.utilization",
        callbacks=[observe],
        unit="1",
        description=f"Fraction of {_DISK_PATH} in use",
    )


def init_metrics() -> object | None:
    """Start CPU / memory / disk gauges, exported to PostHog over OTLP. Idempotent.

    The one question spans and logs cannot answer: was the box healthy when the
    job died.
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
        # Not set as the OTel global — same one-set-per-process trap as the tracer.
        SystemMetricsInstrumentor(config=_SYSTEM_METRICS).instrument(
            meter_provider=_meter_provider
        )
        _add_disk_free_gauge(_meter_provider)
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

    Floors at WARNING — shipping INFO is ~12x the volume for records nobody queries.

    ``uvicorn`` needs an explicit handler (it sets ``propagate = False``), but
    ``uvicorn.error`` must NOT get one: it has no propagate override and bubbles
    up to ``uvicorn``, so handlers on both export every traceback twice.
    ``uvicorn.access`` is excluded — it duplicates the http_request event.
    """
    if _logger_provider is None:
        return
    # An export failure logs through the stdlib and would re-enter this handler.
    logging.getLogger("opentelemetry").propagate = False
    handler = LoggingHandler(level=level, logger_provider=_logger_provider)
    for name in ("", "uvicorn"):
        logging.getLogger(name).addHandler(handler)


def get_tracer_provider() -> TracerProvider | None:
    """The provider built by ``init_otel()``, or None when OTel is off."""
    return _tracer_provider


def shutdown_otel() -> None:
    """Flush and stop all three providers. Never raises.

    Bounded by ``_EXPORT_TIMEOUT_MILLIS`` — see the note there.
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
