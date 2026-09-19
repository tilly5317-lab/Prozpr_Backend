"""Buffered telemetry must survive a restart.

The batch processors hold records in memory; if the process exits without a
flush, the last window of spans and logs — usually the ones explaining WHY it
restarted — is lost.
"""

import logging

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import otel


def test_shutdown_otel_flushes_buffered_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    # Batch (not Simple) — this is what production uses and what buffers.
    provider.add_span_processor(BatchSpanProcessor(exporter))
    monkeypatch.setattr(otel, "_tracer_provider", provider)
    monkeypatch.setattr(otel, "_logger_provider", None)

    with provider.get_tracer("t").start_as_current_span("buffered_work"):
        pass

    assert exporter.get_finished_spans() == (), "should still be buffered"
    otel.shutdown_otel()
    assert [s.name for s in exporter.get_finished_spans()] == ["buffered_work"]


def test_shutdown_otel_flushes_buffered_logs(monkeypatch):
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(otel, "_tracer_provider", None)
    monkeypatch.setattr(otel, "_logger_provider", provider)

    log = logging.getLogger("test.shutdown.flush")
    log.propagate = False
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(LoggingHandler(level=logging.WARNING, logger_provider=provider))
    log.error("last words before restart")

    otel.shutdown_otel()
    assert "last words before restart" in [
        r.log_record.body for r in exporter.get_finished_logs()
    ]


def test_shutdown_otel_is_idempotent(monkeypatch):
    """The lifespan may run it, then an atexit/second call may too."""
    monkeypatch.setattr(otel, "_tracer_provider", None)
    monkeypatch.setattr(otel, "_logger_provider", None)
    otel.shutdown_otel()
    otel.shutdown_otel()  # must not raise


def test_lifespan_shutdown_calls_shutdown_otel(monkeypatch):
    """Wiring guard: the flush is worthless if the lifespan never calls it."""
    import asyncio

    from app.core import lifespan as lifespan_mod

    called = []
    monkeypatch.setattr(lifespan_mod, "shutdown_otel", lambda: called.append(True))
    monkeypatch.setattr(lifespan_mod, "shutdown_posthog", lambda: None)

    async def _noop():
        return None

    monkeypatch.setattr(lifespan_mod, "shutdown_scheduler", _noop)
    monkeypatch.setattr(lifespan_mod, "shutdown_benchmark_scheduler", _noop)
    monkeypatch.setattr(lifespan_mod, "dispose_engine", _noop)

    asyncio.run(lifespan_mod._shutdown())
    assert called == [True]
