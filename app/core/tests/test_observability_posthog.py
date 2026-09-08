"""PostHog backend telemetry helpers in ``observability``.

Two independent signals, deliberately kept apart:
- ``capture_exception`` → 5xx bugs (PostHog Error Tracking, stack traces).
- ``capture_http_request`` → every request as a plain event (durable 12-month
  trend/volume + latency signal, NOT the error-tracking view).

Both reuse the process-wide client built for LLM observability, and both must
NEVER raise into the request path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import app.core.observability as observability


# --------------------------------------------------------------- capture_exception (5xx)


def test_capture_exception_forwards_to_client(monkeypatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)

    err = ValueError("boom")
    observability.capture_exception(err, distinct_id="user-123")

    client.capture_exception.assert_called_once()
    args, kwargs = client.capture_exception.call_args
    assert args[0] is err
    assert kwargs.get("distinct_id") == "user-123"


def test_capture_exception_noop_when_client_disabled(monkeypatch) -> None:
    monkeypatch.setattr(observability, "_posthog_client", None)
    observability.capture_exception(ValueError("boom"))  # must not raise


def test_capture_exception_swallows_client_errors(monkeypatch) -> None:
    client = MagicMock()
    client.capture_exception.side_effect = RuntimeError("sdk down")
    monkeypatch.setattr(observability, "_posthog_client", client)
    observability.capture_exception(ValueError("boom"))  # must not raise


# --------------------------------------------------------- capture_http_request (all)


def test_capture_http_request_forwards_event(monkeypatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)

    observability.capture_http_request(
        status_code=422, path="/api/v1/thing", method="POST", duration_ms=12.345
    )

    client.capture.assert_called_once()
    args, kwargs = client.capture.call_args
    assert args[0] == "http_request"
    props = kwargs["properties"]
    assert props["status_code"] == 422
    assert props["status_class"] == "4xx"
    assert props["path"] == "/api/v1/thing"
    assert props["method"] == "POST"
    assert props["duration_ms"] == 12.35  # rounded to 2dp
    assert props["domain"] == "thing"
    # No user context in a hook → must not create/update a person profile.
    assert props["$process_person_profile"] is False


def test_capture_http_request_skips_untracked_paths(monkeypatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", client)
    observability.capture_http_request(
        status_code=200, path="/api/v1/health", method="GET", duration_ms=1.0
    )
    client.capture.assert_not_called()


def test_capture_http_request_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(observability, "_posthog_client", None)
    observability.capture_http_request(
        status_code=400, path="/x", method="GET", duration_ms=1.0
    )  # must not raise


def test_capture_http_request_swallows_client_errors(monkeypatch) -> None:
    client = MagicMock()
    client.capture.side_effect = RuntimeError("sdk down")
    monkeypatch.setattr(observability, "_posthog_client", client)
    observability.capture_http_request(
        status_code=429, path="/x", method="GET", duration_ms=1.0
    )  # must not raise
