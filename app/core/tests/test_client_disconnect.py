"""A client hanging up is not a server error."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app.core import observability
from app.core.exceptions import register_exception_handlers


@pytest.fixture
def captured(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)
    yield client
    monkeypatch.setattr(observability, "_posthog_client", None)


def test_client_disconnect_is_499_and_not_captured(captured):
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/api/v1/upload")
    async def upload():
        raise ClientDisconnect()

    resp = TestClient(app, raise_server_exceptions=False).post("/api/v1/upload")

    assert resp.status_code == 499
    assert captured.capture_exception.call_count == 0


def test_a_real_error_still_becomes_500_and_is_captured(captured):
    """Guard the guard: the 499 handler must not swallow genuine failures."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/api/v1/kaboom")
    async def kaboom():
        raise ValueError("genuinely broken")

    resp = TestClient(app, raise_server_exceptions=False).post("/api/v1/kaboom")

    assert resp.status_code == 500
    assert captured.capture_exception.call_count == 1
