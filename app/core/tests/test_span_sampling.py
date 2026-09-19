"""Internet background noise must never become spans — only served paths sample."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision, ParentBased

from app.core.otel import _ServedPathSampler


def _decision(path: str | None) -> Decision:
    return (
        _ServedPathSampler("/api/v1")
        .should_sample(
            None,
            1,
            "GET",
            attributes={"url.path": path} if path is not None else None,
        )
        .decision
    )


def test_scanner_paths_are_dropped():
    # Every one of these was observed hitting the public IP in production.
    for path in ("/announce", "/scrape", "/.env", "/wp-includes/x.php", "/api/.env"):
        assert _decision(path) is Decision.DROP, path


def test_served_paths_are_sampled():
    assert _decision("/api/v1/goals/3") is Decision.RECORD_AND_SAMPLE


def test_legacy_semconv_path_attribute_is_honoured():
    """Without OTEL_SEMCONV_STABILITY_OPT_IN the ASGI layer emits http.target."""
    result = _ServedPathSampler("/api/v1").should_sample(
        None, 1, "GET", attributes={"http.target": "/announce"}
    )
    assert result.decision is Decision.DROP


def test_spans_carrying_no_path_are_sampled():
    """Schedulers and LLM chains have no request path — they must not be filtered."""
    assert _decision(None) is Decision.RECORD_AND_SAMPLE


def test_child_spans_of_a_dropped_request_are_dropped_too():
    """The `http send` children outnumber the server spans they belong to.

    Dropping only the server span would leave them as orphans, so the sampler is
    wrapped in ParentBased: an unsampled parent takes its whole subtree with it.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ParentBased(root=_ServedPathSampler("/api/v1")))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    app = FastAPI()

    @app.get("/api/v1/ok")
    def ok():
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    client = TestClient(app)

    client.get("/announce")
    assert exporter.get_finished_spans() == (), "scanner traffic produced spans"

    client.get("/api/v1/ok")
    assert len(exporter.get_finished_spans()) > 0, "served traffic produced no spans"
