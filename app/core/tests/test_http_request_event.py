"""http_request: route TEMPLATE, true status (incl. 5xx), excluded noise paths."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from app.core import observability


@pytest.fixture
def captured(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)
    yield client
    monkeypatch.setattr(observability, "_posthog_client", None)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/goals/{goal_id}")
    def one(goal_id: str):
        return {"id": goal_id}

    @app.get("/api/v1/boom")
    def boom():
        raise ValueError("kaboom")

    @app.get("/api/v1/health")
    def health():
        return {"ok": True}

    @app.get("/api/v1/slow")
    def slow():
        time.sleep(0.05)
        return {"ok": True}

    @app.get("/api/v1/missing")
    def missing():
        raise HTTPException(status_code=404, detail="no such goal")

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=TracerProvider(),
        server_request_hook=observability.otel_request_hook,
        client_response_hook=observability.otel_response_hook,
    )
    return app


def _events(client) -> list[tuple[str, dict]]:
    return [
        (c.args[0], c.kwargs["properties"])
        for c in client.capture.call_args_list
        if c.args and c.args[0] == "http_request"
    ]


def test_path_is_route_template_not_raw_path(captured):
    TestClient(_app()).get("/api/v1/goals/8f3a-secret-uuid")
    ((_, props),) = _events(captured)
    assert props["path"] == "/api/v1/goals/{goal_id}"
    assert "8f3a-secret-uuid" not in str(props)
    assert props["status_code"] == 200
    assert props["method"] == "GET"
    assert props["duration_ms"] >= 0
    assert props["$process_person_profile"] is False


def test_unhandled_exception_reports_500(captured):
    TestClient(_app(), raise_server_exceptions=False).get("/api/v1/boom")
    ((_, props),) = _events(captured)
    assert props["status_code"] == 500
    assert props["path"] == "/api/v1/boom"


def test_health_is_excluded(captured):
    TestClient(_app()).get("/api/v1/health")
    assert _events(captured) == []


def test_unmatched_route_is_excluded(captured):
    """Requests that match no route are internet background noise, not signal.

    On the public IP these were 93% of all http_request events — BitTorrent
    tracker probes, .env hunting, PHP shell scans — and they drag the aggregate
    4xx rate to ~93% while the real client-error rate on served routes is <1%.
    """
    TestClient(_app()).get("/api/v1/nope")
    assert _events(captured) == []


def test_404_from_a_matched_route_is_still_reported(captured):
    """The exclusion is on the unmatched ROUTE, not on the 404 status.

    A handler answering "no such goal" is a real signal about a real endpoint.
    """
    TestClient(_app()).get("/api/v1/missing")
    ((_, props),) = _events(captured)
    assert props["path"] == "/api/v1/missing"
    assert props["status_code"] == 404


def test_duration_measures_the_REQUEST_not_the_send_span(captured):
    """The response hook is handed the ``http send`` CHILD span.

    Timing from that span's start_time measures the few microseconds spent
    emitting the response, not the request — a ~50ms handler would report
    ~0.1ms. The duration must come from the server span via the ASGI scope.
    """
    TestClient(_app()).get("/api/v1/slow")
    ((_, props),) = _events(captured)
    assert props["duration_ms"] >= 40, (
        f"duration_ms={props['duration_ms']} — this is the send-span duration, "
        "not the request duration."
    )
