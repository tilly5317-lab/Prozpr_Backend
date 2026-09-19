"""Regression guard: OTel must actually be in the live middleware stack.

Instrumenting in the wrong place is a SILENT no-op (Starlette caches
middleware_stack on first __call__), so assert on the real chain, not on
whether the instrumentor object exists.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware


def _stack_contains_otel(app: FastAPI) -> bool:
    node = app.middleware_stack
    seen = 0
    while node is not None and seen < 25:
        if isinstance(node, OpenTelemetryMiddleware):
            return True
        node = getattr(node, "app", None)
        seen += 1
    return False


def test_app_middleware_stack_contains_otel_middleware():
    from app.main import app

    # Force the stack to be built exactly as production builds it.
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/api/v1/health")

    assert _stack_contains_otel(app), (
        "OpenTelemetryMiddleware is missing from the live stack — instrument_app() "
        "was probably called after the stack was cached (e.g. inside lifespan)."
    )
