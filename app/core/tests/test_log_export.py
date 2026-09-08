"""Logs reach the exporter, below-floor records don't, and PII is scrubbed."""

import logging

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from app.core.log_scrubber import ScrubbingLogProcessor


def _provider_with_exporter():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    # Registration order is execution order — the scrubber must precede the export.
    provider.add_log_record_processor(ScrubbingLogProcessor())
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    return provider, exporter


def _logger(name: str, provider) -> logging.Logger:
    log = logging.getLogger(name)
    log.propagate = False
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(LoggingHandler(level=logging.WARNING, logger_provider=provider))
    return log


def _bodies(exporter) -> list[str]:
    return [r.log_record.body for r in exporter.get_finished_logs()]


def test_warning_is_exported_and_info_is_not():
    provider, exporter = _provider_with_exporter()
    log = _logger("test.export", provider)

    log.info("this should not ship")
    log.warning("this should ship")
    provider.force_flush()

    bodies = _bodies(exporter)
    assert "this should ship" in bodies
    assert "this should not ship" not in bodies


def test_phone_number_is_scrubbed():
    provider, exporter = _provider_with_exporter()
    log = _logger("test.scrub", provider)

    log.warning("failed for 919876543210")
    provider.force_flush()

    body = _bodies(exporter)[0]
    assert "919876543210" not in body
    assert "[REDACTED]" in body


def test_pan_and_email_are_scrubbed():
    provider, exporter = _provider_with_exporter()
    log = _logger("test.scrub2", provider)

    log.warning("user ABCDE1234F at someone@example.com failed")
    provider.force_flush()

    body = _bodies(exporter)[0]
    assert "ABCDE1234F" not in body
    assert "someone@example.com" not in body


def test_scrubbing_survives_to_the_exporter_not_just_the_processor():
    """Regression: mutating the wrong object scrubs nothing and raises nothing.

    ``on_emit`` receives a ReadWriteLogRecord, which has NO ``body`` attribute —
    assigning ``record.body`` there silently creates a stray attribute while the
    real body ships unredacted. The body lives at ``record.log_record.body``.
    """
    provider, exporter = _provider_with_exporter()
    log = _logger("test.scrub3", provider)

    log.warning("leak 919876543210")
    provider.force_flush()

    assert "919876543210" not in _bodies(exporter)[0]


def test_non_string_body_is_left_alone():
    provider, exporter = _provider_with_exporter()
    log = _logger("test.scrub4", provider)

    log.warning({"structured": "payload"})
    provider.force_flush()

    assert exporter.get_finished_logs()  # must not have raised
