"""Defence-in-depth redaction for log bodies leaving the process.

A backstop, not a licence to log PII — call sites are still fixed at source.

The patterns themselves live in ``app.core.pii`` so that this exporter, the
PostHog exception path (``app.core.observability.capture_exception``) and the
third-party clients all redact to the same rules. They used to be duplicated
here, which is how lower-cased PAN stayed readable in one pipeline after being
scrubbed in another.
"""

from __future__ import annotations

from opentelemetry.sdk._logs import LogRecordProcessor

from app.core.pii import redact_obj, redact_text


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
            # Bodies are usually strings, but a structured log ships a dict or a
            # list — those used to be returned untouched, which meant the one
            # shape most likely to hold a whole request payload was the one shape
            # that skipped scrubbing entirely.
            inner.body = redact_text(body) if isinstance(body, str) else redact_obj(body)
        except Exception:  # pragma: no cover - a processor must never raise
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
