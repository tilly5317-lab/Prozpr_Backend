"""Any logged exception becomes an Error Tracking issue — once, and throttled.

74 call sites log an exception with a full stack trace; before this handler, 2
of them reached Error Tracking and the other 72 produced a log line that expires
in ~14 days and cannot be counted or grouped.
"""

import logging
import sys

import pytest

from app.core import error_capture, observability


@pytest.fixture
def captured(monkeypatch):
    seen: list[BaseException] = []
    monkeypatch.setattr(
        observability, "capture_exception", lambda exc, **kw: seen.append(exc)
    )
    error_capture._last_sent.clear()
    yield seen
    error_capture._last_sent.clear()


def _emit(exc: BaseException, *, logger_name: str = "app.jobs", lineno: int = 42):
    """Build a record carrying real exc_info and push it through the handler."""
    handler = error_capture.ErrorTrackingHandler(level=logging.WARNING)
    try:
        raise exc
    except BaseException:
        record = logging.LogRecord(
            logger_name, logging.ERROR, __file__, lineno, "boom", (), sys.exc_info()
        )
    handler.emit(record)


def test_a_logged_exception_is_reported(captured):
    _emit(ValueError("kaboom"))
    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)


def test_a_record_without_exc_info_is_ignored(captured):
    """logger.error("...") with no exception has nothing to group on."""
    handler = error_capture.ErrorTrackingHandler(level=logging.WARNING)
    handler.emit(
        logging.LogRecord("app", logging.ERROR, __file__, 1, "no exc", (), None)
    )
    assert captured == []


def test_the_same_site_is_throttled(captured):
    """networth_history_service.py:367 logs per SCHEME inside a loop. Without a
    throttle, one mfapi outage files thousands of issues from one code line."""
    for _ in range(50):
        _emit(ValueError("same site"))
    assert len(captured) == 1


def test_a_different_exception_type_is_not_throttled(captured):
    _emit(ValueError("one"))
    _emit(KeyError("two"))
    assert len(captured) == 2


def test_a_different_code_line_is_not_throttled(captured):
    _emit(ValueError("x"), lineno=10)
    _emit(ValueError("x"), lineno=99)
    assert len(captured) == 2


def test_a_different_logger_is_not_throttled(captured):
    _emit(ValueError("x"), logger_name="app.jobs")
    _emit(ValueError("x"), logger_name="app.chat")
    assert len(captured) == 2


def test_a_reporting_failure_never_propagates(captured, monkeypatch):
    """A logging handler that raises breaks the log call that invoked it."""

    def explode(exc, **kw):
        raise RuntimeError("posthog down")

    monkeypatch.setattr(observability, "capture_exception", explode)
    _emit(ValueError("kaboom"))


def test_reentrancy_is_blocked(captured, monkeypatch):
    """If PostHog's own SDK logs an exception while we are reporting one, the
    handler must not recurse into itself."""
    depth = {"max": 0, "cur": 0}

    def reentrant(exc, **kw):
        depth["cur"] += 1
        depth["max"] = max(depth["max"], depth["cur"])
        _emit(RuntimeError("sdk failure while reporting"))
        depth["cur"] -= 1
        captured.append(exc)

    monkeypatch.setattr(observability, "capture_exception", reentrant)
    _emit(ValueError("original"))
    assert depth["max"] == 1


def test_attach_is_disabled_by_the_env_flag(monkeypatch):
    monkeypatch.setenv("POSTHOG_ERROR_CAPTURE_ENABLED", "false")
    root = logging.getLogger("")
    before = list(root.handlers)
    error_capture.attach_error_capture()
    added = [h for h in root.handlers if h not in before]
    for h in added:
        root.removeHandler(h)
    assert added == []


def test_attach_adds_the_handler_when_enabled(monkeypatch):
    monkeypatch.setenv("POSTHOG_ERROR_CAPTURE_ENABLED", "true")
    root = logging.getLogger("")
    before = list(root.handlers)
    error_capture.attach_error_capture()
    added = [h for h in root.handlers if h not in before]
    try:
        assert any(isinstance(h, error_capture.ErrorTrackingHandler) for h in added)
    finally:
        for h in added:
            root.removeHandler(h)
        uvicorn_log = logging.getLogger("uvicorn")
        for h in list(uvicorn_log.handlers):
            if isinstance(h, error_capture.ErrorTrackingHandler):
                uvicorn_log.removeHandler(h)


def test_capture_exception_files_an_exception_only_once(monkeypatch):
    """The 500 handler, the job reporter, and this handler can all see the same
    exception. Every one SHOULD mark its own span; the issue files once."""
    client = type("C", (), {"calls": 0})()

    def capture_exception(exc, **kw):
        client.calls += 1

    monkeypatch.setattr(observability, "_posthog_client", client)
    monkeypatch.setattr(
        type(client), "capture_exception", staticmethod(capture_exception), raising=False
    )

    exc = ValueError("one failure, three reporters")
    observability.capture_exception(exc)
    observability.capture_exception(exc)
    observability.capture_exception(exc)
    monkeypatch.setattr(observability, "_posthog_client", None)

    assert client.calls == 1
