"""Which loggers the OTLP handler attaches to, under uvicorn's real config."""

import logging
import logging.config

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from uvicorn.config import LOGGING_CONFIG

from app.core import otel


def _attached(monkeypatch):
    """Install the real handler set against an in-memory exporter."""
    logging.config.dictConfig(LOGGING_CONFIG)  # exactly what uvicorn does at boot
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(otel, "_logger_provider", provider)
    otel.attach_otel_logging()
    return provider, exporter


def _teardown(provider):
    for name in ("", "uvicorn"):
        lg = logging.getLogger(name)
        for h in [h for h in lg.handlers if isinstance(h, LoggingHandler)]:
            lg.removeHandler(h)
    provider.shutdown()


def test_uvicorn_traceback_is_exported_exactly_once(monkeypatch):
    """``uvicorn.error`` propagates to ``uvicorn``.

    Attaching to both exports every ASGI traceback twice — double volume on the
    records that matter most, and double-counted error rates.
    """
    provider, exporter = _attached(monkeypatch)
    try:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")
        provider.force_flush()
        assert len(exporter.get_finished_logs()) == 1
    finally:
        _teardown(provider)


def test_application_logger_is_exported_once(monkeypatch):
    provider, exporter = _attached(monkeypatch)
    try:
        logging.getLogger("app.core.whatever").error("a normal app error")
        provider.force_flush()
        assert len(exporter.get_finished_logs()) == 1
    finally:
        _teardown(provider)


def test_access_log_is_not_exported(monkeypatch):
    """It duplicates http_request at 1/180th the retention."""
    provider, exporter = _attached(monkeypatch)
    try:
        logging.getLogger("uvicorn.access").error("GET /api/v1/thing 200")
        provider.force_flush()
        assert exporter.get_finished_logs() == ()
    finally:
        _teardown(provider)


def test_attach_is_a_noop_when_otel_is_disabled(monkeypatch):
    monkeypatch.setattr(otel, "_logger_provider", None)
    otel.attach_otel_logging()  # must not raise
    root = logging.getLogger("")
    assert not any(isinstance(h, LoggingHandler) for h in root.handlers)
