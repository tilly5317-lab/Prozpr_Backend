"""5xx errors are attributed to the authenticated user when there is one."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import observability
from app.core.exceptions import register_exception_handlers


@pytest.fixture
def captured(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)
    yield client
    monkeypatch.setattr(observability, "_posthog_client", None)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/v1/known/{thing_id}")
    async def known(request: Request, thing_id: str):
        request.state.distinct_id = "user-77"
        raise ValueError("boom")

    @app.get("/api/v1/anon")
    async def anon():
        raise ValueError("boom")

    return app


def test_authenticated_5xx_carries_the_user_id(captured):
    TestClient(_app(), raise_server_exceptions=False).get("/api/v1/known/abc")
    assert captured.capture_exception.call_args.kwargs["distinct_id"] == "user-77"


def test_anonymous_5xx_does_not_create_a_person(captured):
    TestClient(_app(), raise_server_exceptions=False).get("/api/v1/anon")
    kwargs = captured.capture_exception.call_args.kwargs
    assert kwargs.get("distinct_id") is None
    assert kwargs["properties"]["$process_person_profile"] is False


def test_get_current_user_dependency_resolves_and_sets_distinct_id():
    """The auth dependency must actually populate ``request.state.distinct_id``.

    Importing ``app.main`` is NOT a guard here: with ``from __future__ import
    annotations`` the ``Request`` annotation is a string, so a missing import
    boots fine and only fails when a real request resolves the dependency.
    """
    from fastapi.dependencies.utils import get_dependant

    from app.core.dependencies import get_current_user

    dep = get_dependant(path="/api/v1/x", call=get_current_user)
    assert dep.request_param_name == "request", (
        "get_current_user no longer receives the Request — request.state."
        "distinct_id would never be set and every 5xx loses its user."
    )


def test_exception_path_is_the_route_template(captured):
    """Same cardinality fix as http_request — raw paths embed user ids."""
    TestClient(_app(), raise_server_exceptions=False).get(
        "/api/v1/known/8f3a-secret-uuid"
    )
    props = captured.capture_exception.call_args.kwargs["properties"]
    assert props["path"] == "/api/v1/known/{thing_id}"
    assert "8f3a-secret-uuid" not in str(props)
    assert props["domain"] == "known"
