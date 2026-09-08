"""Background jobs must produce spans and Error Tracking issues, or stay silent when OTel is off."""

from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core import job_tracing, observability


@pytest.fixture
def exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(job_tracing, "_tracer", lambda: provider.get_tracer("test"))
    return exporter


@pytest.fixture
def posthog(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)
    yield client
    monkeypatch.setattr(observability, "_posthog_client", None)


def test_successful_run_produces_a_span_with_attributes(exporter, posthog):
    with job_tracing.job_span("mfapi.daily_job", schemes=8000):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.name == "mfapi.daily_job"
    assert span.attributes["schemes"] == 8000
    assert span.status.status_code is not StatusCode.ERROR
    posthog.capture_exception.assert_not_called()


def test_failure_marks_span_reports_and_reraises(exporter, posthog):
    """Observing a failure must never swallow it."""
    with pytest.raises(ValueError, match="numeric field overflow"):
        with job_tracing.job_span("mfapi.daily_job", phase=3):
            raise ValueError("numeric field overflow")

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [e.name for e in span.events] == ["exception"]

    posthog.capture_exception.assert_called_once()
    props = posthog.capture_exception.call_args.kwargs["properties"]
    assert props == {"job": "mfapi.daily_job", "phase": 3}


def test_report_job_failure_handles_an_already_caught_exception(exporter, posthog):
    """The schedulers catch and log rather than raise — that path must still report."""
    with job_tracing.job_span("benchmark.refresh") as span:
        try:
            raise RuntimeError("niftyindices unreachable")
        except RuntimeError as exc:
            job_tracing.report_job_failure(exc, job="benchmark.refresh", source="nifty")
        assert span.is_recording()

    (finished,) = exporter.get_finished_spans()
    assert finished.status.status_code is StatusCode.ERROR
    posthog.capture_exception.assert_called_once()


def test_nested_spans_all_mark_error_but_file_one_issue(exporter, posthog):
    """run > phase both go red so the trace reads correctly — but one issue, not two."""
    with pytest.raises(ValueError):
        with job_tracing.job_span("mfapi.daily_job"):
            with job_tracing.job_span("mfapi.daily_job.phase", phase=3):
                raise ValueError("numeric field overflow")

    spans = exporter.get_finished_spans()
    assert {s.name for s in spans} == {"mfapi.daily_job", "mfapi.daily_job.phase"}
    assert all(s.status.status_code is StatusCode.ERROR for s in spans)
    assert posthog.capture_exception.call_count == 1
    # Filed against the innermost frame — the phase is where the detail is.
    assert posthog.capture_exception.call_args.kwargs["properties"]["phase"] == 3


def test_reporting_never_raises_into_the_job(exporter):
    """PostHog being down must not turn a handled job failure into an unhandled one."""
    with job_tracing.job_span("mfapi.daily_job"):
        job_tracing.report_job_failure(ValueError("x"), job="mfapi.daily_job")


async def test_traced_job_decorator_spans_an_async_entry_point(exporter, posthog):
    @job_tracing.traced_job("mfapi.daily_job")
    async def run(n):
        return n * 2

    assert await run(21) == 42
    (span,) = exporter.get_finished_spans()
    assert span.name == "mfapi.daily_job"
    assert run.__name__ == "run", "functools.wraps must preserve the job's identity"


async def test_traced_job_reports_and_reraises(exporter, posthog):
    @job_tracing.traced_job("benchmark.refresh")
    async def run():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await run()
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    posthog.capture_exception.assert_called_once()


def test_no_op_when_otel_is_disabled(monkeypatch, posthog):
    """OTel off (no POSTHOG_API_KEY) must not break the job."""
    monkeypatch.setattr("app.core.otel.get_tracer_provider", lambda: None)
    with job_tracing.job_span("mfapi.daily_job", phase=1) as span:
        assert not span.is_recording()


# --- job_completed: the event that makes a job's absence detectable ---------


async def test_traced_job_emits_job_completed_on_success(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))

    @job_tracing.traced_job("test.job")
    async def work():
        job_tracing.record_job_counts(rows=7)
        return "done"

    assert await work() == "done"
    assert sent[0]["job"] == "test.job"
    assert sent[0]["outcome"] == "ok"
    assert sent[0]["failure_reason"] is None
    assert sent[0]["counts"] == {"rows": 7}
    assert sent[0]["duration_ms"] >= 0


async def test_traced_job_emits_failed_when_the_job_raises(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))
    monkeypatch.setattr(observability, "capture_exception", lambda exc, **kw: None)

    @job_tracing.traced_job("test.job")
    async def work():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        await work()
    assert sent[0]["outcome"] == "failed"
    assert sent[0]["failure_reason"] == "ValueError"


async def test_a_caught_and_reported_failure_still_marks_the_run_failed(monkeypatch):
    """The real scheduler shape: the job swallows its own exception and returns
    normally, so the decorator's except never fires. report_job_failure must
    still flip the outcome — otherwise a crashed job reports success, which is
    exactly what happened to the mfapi NUMERIC-overflow crash."""
    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))
    monkeypatch.setattr(observability, "capture_exception", lambda exc, **kw: None)

    @job_tracing.traced_job("test.job")
    async def work():
        try:
            raise ValueError("kaboom")
        except ValueError as exc:
            job_tracing.report_job_failure(exc, job="test.job")
        return "swallowed"

    assert await work() == "swallowed"
    assert sent[0]["outcome"] == "failed"
    assert sent[0]["failure_reason"] == "ValueError"


async def test_record_job_counts_outside_a_job_is_a_no_op():
    job_tracing.record_job_counts(rows=1)


async def test_concurrent_jobs_do_not_share_counts(monkeypatch):
    """ContextVar, not a module global: two jobs in different tasks must never
    see each other's numbers."""
    import asyncio

    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))

    @job_tracing.traced_job("test.a")
    async def job_a():
        job_tracing.record_job_counts(rows=1)
        await asyncio.sleep(0.01)
        return None

    @job_tracing.traced_job("test.b")
    async def job_b():
        job_tracing.record_job_counts(rows=999)
        return None

    await asyncio.gather(job_a(), job_b())
    by_job = {s["job"]: s["counts"] for s in sent}
    assert by_job["test.a"] == {"rows": 1}
    assert by_job["test.b"] == {"rows": 999}
