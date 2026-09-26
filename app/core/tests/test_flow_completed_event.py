"""flow_completed: the event that makes "rebalancing failed" a number.

Every AI flow enters through one endpoint and returns 201 whether it produced a
plan or an apology, so HTTP status can never tell them apart. This is the only
signal that can.
"""

from unittest.mock import MagicMock

import pytest

from app.core import observability


@pytest.fixture
def client(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", c)
    yield c
    monkeypatch.setattr(observability, "_posthog_client", None)


def _sent(client) -> tuple[str, dict, dict]:
    call = client.capture.call_args
    return call[0][0], call.kwargs["properties"], call.kwargs


def test_success_is_recorded_with_no_reason(client):
    observability.capture_flow_completed(
        intent="rebalancing",
        outcome="ok",
        failure_reason=None,
        duration_ms=1234.5,
        distinct_id="user-1",
    )
    name, props, kwargs = _sent(client)
    assert name == "flow_completed"
    assert props["intent"] == "rebalancing"
    assert props["outcome"] == "ok"
    assert props["failure_reason"] is None
    assert props["duration_ms"] == 1234.5
    assert kwargs["distinct_id"] == "user-1"


def test_failure_carries_the_reason(client):
    observability.capture_flow_completed(
        intent="rebalancing",
        outcome="failed",
        failure_reason="timeout",
        duration_ms=60000.0,
        distinct_id="user-1",
    )
    _, props, _ = _sent(client)
    assert props["outcome"] == "failed"
    assert props["failure_reason"] == "timeout"


def test_missing_user_falls_back_to_backend(client):
    observability.capture_flow_completed(
        intent=None,
        outcome="ok",
        failure_reason=None,
        duration_ms=5.0,
        distinct_id=None,
    )
    _, _, kwargs = _sent(client)
    assert kwargs["distinct_id"] == "backend"


def test_uuid_user_ids_are_stringified(client):
    import uuid

    uid = uuid.uuid4()
    observability.capture_flow_completed(
        intent="chat",
        outcome="ok",
        failure_reason=None,
        duration_ms=1.0,
        distinct_id=uid,
    )
    _, _, kwargs = _sent(client)
    assert kwargs["distinct_id"] == str(uid)


def test_never_raises_when_the_client_explodes(client):
    client.capture.side_effect = RuntimeError("posthog down")
    observability.capture_flow_completed(
        intent="x",
        outcome="ok",
        failure_reason=None,
        duration_ms=1.0,
        distinct_id="u",
    )


def test_no_client_is_a_silent_no_op(monkeypatch):
    monkeypatch.setattr(observability, "_posthog_client", None)
    observability.capture_flow_completed(
        intent="x",
        outcome="ok",
        failure_reason=None,
        duration_ms=1.0,
        distinct_id="u",
    )
