"""Defence-in-depth redaction for log bodies leaving the process.

A backstop, not a licence to log PII — call sites are still fixed at source.
Deliberately narrow: broad patterns mangle order ids and amounts, making
incidents harder to read.
"""

from __future__ import annotations

import re

from opentelemetry.sdk._logs import LogRecordProcessor

_PATTERNS = (
    # Indian mobile numbers, with or without the 91 country code.
    re.compile(r"\b(?:\+?91[\-\s]?)?[6-9]\d{9}\b"),
    # PAN: 5 letters, 4 digits, 1 letter.
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    # Email addresses.
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
)


class ScrubbingLogProcessor(LogRecordProcessor):
    """Rewrites matched PII in the log body to ``[REDACTED]``.

    Register this BEFORE the exporting processor — ``LoggerProvider`` runs
    processors in registration order, so a scrubber added afterwards edits a
    record that has already been handed to the exporter.
    """

    def on_emit(self, log_record) -> None:
        # ``log_record`` is a ReadWriteLogRecord, which has no ``body`` of its own;
        # the mutable body lives on the LogRecord it wraps. Assigning to
        # ``log_record.body`` here would silently create a stray attribute and ship
        # the original text — see test_log_export.py for the regression guard.
        try:
            inner = log_record.log_record
            body = inner.body
            if not isinstance(body, str):
                return
            for pattern in _PATTERNS:
                body = pattern.sub("[REDACTED]", body)
            inner.body = body
        except Exception:  # pragma: no cover - a processor must never raise
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
