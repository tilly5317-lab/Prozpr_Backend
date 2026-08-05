# Backend Failure Tracking + PostHog Dashboards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backend failures countable — chat flows that return an apology, jobs that fail or stop running, and the 72 exceptions currently swallowed into expiring log lines — then build the dashboards that read them.

**Architecture:** Three new durable PostHog events (`flow_completed`, `job_completed`, plus a repaired `domain` property on existing events) and one logging handler that forwards any logged exception to Error Tracking. Instrumentation sits at choke points that already exist — `ChatBrain._finalize`, the `@traced_job` decorator, the root logger — so new intents, new jobs, and new `except` blocks are covered without further edits.

**Tech Stack:** FastAPI, PostHog Python SDK (`posthog>=6.7.13`), OpenTelemetry SDK 1.44.0, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-08-01-observability-dashboards-design.md`

## Global Constraints

- **Do NOT commit.** The user commits everything themselves at the end. Every task ends with `git add` staging only — never `git commit`.
- **Run tests with `.venv-mac/bin/python -m pytest`.** Same prefix for any other tool.
- **Telemetry must never raise into the request or job path.** Every new emitter wraps its body in `try/except Exception: pass`.
- **`app/core/tests/` is gitignored** (`.gitignore:121`). Tests there cannot be staged or committed. Write them anyway — they must pass locally — but expect `git add` to skip them.
- **Never put `str(exc)` in an event property.** Exception messages embed user data. Use `type(exc).__name__`.
- **Baseline, measured 2026-08-01:** `.venv-mac/bin/python -m pytest app/ -q --continue-on-collection-errors` → **531 passed, 28 failed, 3 skipped, 15 errors**. That is the bar; neither failure count may grow.
  `--continue-on-collection-errors` and the `app/` scope are both required: a bare `pytest -q` aborts on 10 pre-existing collection errors in `AI_Agents/archive/`, `scripts/`, and stale tests importing removed symbols, and runs nothing at all. None of them are in `app/core`.
- **Do not boot the app locally.** `.env`'s `DATABASE_URL` points at the production RDS; `uvicorn main:app` runs DDL against it.

---

### Task 1: Fix the `service` → `domain` collision

A live bug on deployed `main`. `capture_http_request` computes a per-domain value and passes it as `service`; `super_properties` overwrites it. 63,595 events, one bucket. Independent of everything else — ship it first.

**Files:**
- Modify: `app/core/observability.py` (in `capture_http_request`)
- Modify: `app/core/exceptions.py:96-105` (the `capture_exception` properties dict)
- Test: `app/core/tests/test_observability_super_properties.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: event property `domain: str` on `http_request` and `$exception`. Task 7 queries `properties.domain`.

- [ ] **Step 1: Write the failing test**

Append to `app/core/tests/test_observability_super_properties.py`:

```python
def test_domain_survives_the_super_properties_merge(monkeypatch):
    """posthog/client.py:1666 merges super_properties SECOND, so they overwrite
    same-named event properties. A MagicMock client cannot see this — it records
    what we passed, not what the SDK would send. Use a real client and capture
    via before_send, which fires after the merge and drops the event when it
    returns None (so nothing leaves the process)."""
    from posthog import Posthog

    captured: list[dict] = []
    client = Posthog(
        "phc_test",
        host="https://example.invalid",
        super_properties={"service": "prozpr-backend", "environment": "test"},
        before_send=lambda msg: captured.append(msg) or None,
    )
    monkeypatch.setattr(observability, "_posthog_client", client)

    observability.capture_http_request(
        status_code=200,
        path="/api/v1/goals/{goal_id}",
        method="GET",
        duration_ms=12.0,
    )
    monkeypatch.setattr(observability, "_posthog_client", None)

    assert captured, "before_send never fired — the event never reached the merge"
    props = captured[0]["properties"]
    assert props["domain"] == "goals"
    assert props["service"] == "prozpr-backend"
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_observability_super_properties.py -v
```

Expected: FAIL with `KeyError: 'domain'`. If it fails any other way, stop — the harness is wrong, not the code.

- [ ] **Step 3: Rename the property in both emitters**

In `app/core/observability.py`, inside `capture_http_request`'s properties dict:

```python
                "duration_ms": round(duration_ms, 2),
                "domain": _service_from_path(path),
                "$process_person_profile": False,
```

In `app/core/exceptions.py`, inside the `capture_exception(...)` call:

```python
            properties={
                "path": path,
                "error_type": type(exc).__name__,
                "domain": _service_from_path(path),
                **({} if distinct_id else {"$process_person_profile": False}),
            },
```

- [ ] **Step 4: Run the test plus the neighbours it could break**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_observability_super_properties.py app/core/tests/test_http_request_event.py app/core/tests/test_exception_identity.py -v
```

Expected: all PASS. `test_http_request_event.py` asserts on `client.capture` call args; if any assertion names `service`, update it to `domain`.

- [ ] **Step 5: Stage (do not commit)**

```bash
git add app/core/observability.py app/core/exceptions.py
```

---

### Task 2: `flow_completed` — make chat failures countable

**Files:**
- Modify: `app/core/observability.py` (add `capture_flow_completed`)
- Modify: `app/domains/ai_engine/services/brain.py` (`_finalize` + its 4 call sites)
- Test: `app/core/tests/test_flow_completed_event.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `capture_flow_completed(*, intent: str | None, outcome: str, failure_reason: str | None, duration_ms: float, distinct_id) -> None`. Emits event `flow_completed`.

**Deviation from spec:** the spec listed a `flow` property alongside `intent`. Dropped — `FLOWS` maps intent to flow 1:1, so it is redundant. Add it later if unknown-intent fallthrough turns out to matter.

- [ ] **Step 1: Write the failing test**

Create `app/core/tests/test_flow_completed_event.py`:

```python
"""flow_completed: the event that makes 'rebalancing failed' a number."""

from unittest.mock import MagicMock

import pytest

from app.core import observability


@pytest.fixture
def client(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(observability, "_posthog_client", c)
    yield c
    monkeypatch.setattr(observability, "_posthog_client", None)


def test_success_is_recorded_with_no_reason(client):
    observability.capture_flow_completed(
        intent="rebalancing", outcome="ok", failure_reason=None,
        duration_ms=1234.5, distinct_id="user-1",
    )
    name, props = client.capture.call_args[0][0], client.capture.call_args.kwargs["properties"]
    assert name == "flow_completed"
    assert props["intent"] == "rebalancing"
    assert props["outcome"] == "ok"
    assert props["failure_reason"] is None
    assert client.capture.call_args.kwargs["distinct_id"] == "user-1"


def test_failure_carries_the_reason(client):
    observability.capture_flow_completed(
        intent="rebalancing", outcome="failed", failure_reason="timeout",
        duration_ms=60000.0, distinct_id="user-1",
    )
    assert client.capture.call_args.kwargs["properties"]["failure_reason"] == "timeout"


def test_missing_user_falls_back_to_backend(client):
    observability.capture_flow_completed(
        intent=None, outcome="ok", failure_reason=None,
        duration_ms=5.0, distinct_id=None,
    )
    assert client.capture.call_args.kwargs["distinct_id"] == "backend"


def test_never_raises_when_the_client_explodes(client):
    client.capture.side_effect = RuntimeError("posthog down")
    observability.capture_flow_completed(
        intent="x", outcome="ok", failure_reason=None, duration_ms=1.0, distinct_id="u",
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_flow_completed_event.py -v
```

Expected: FAIL, `AttributeError: module 'app.core.observability' has no attribute 'capture_flow_completed'`.

- [ ] **Step 3: Add the emitter**

In `app/core/observability.py`, after `capture_http_request`:

```python
def capture_flow_completed(
    *,
    intent: str | None,
    outcome: str,
    failure_reason: str | None,
    duration_ms: float,
    distinct_id: object | None,
) -> None:
    """Record the outcome of one chat turn as a durable event.

    Every AI flow enters through one endpoint and returns 201 whether it
    produced a plan or an apology, so HTTP status can never distinguish them.
    This is the only signal that can.

    ``failure_reason`` is an exception CLASS name or a fixed token — never
    ``str(exc)``, which embeds user data into a 12-month analytics property.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "flow_completed",
            distinct_id=str(distinct_id) if distinct_id else "backend",
            properties={
                "intent": intent,
                "outcome": outcome,
                "failure_reason": failure_reason,
                "duration_ms": round(duration_ms, 2),
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass
```

- [ ] **Step 4: Run the test**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_flow_completed_event.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Wire it into `_finalize`**

In `app/domains/ai_engine/services/brain.py`, add two keyword params to `_finalize` (after `usage_cb=None`):

```python
        usage_cb=None,
        outcome: str = "ok",
        failure_reason: str | None = None,
    ) -> ChatBrainResult:
```

Then immediately before the closing `return ChatBrainResult(`:

```python
        from app.core.observability import capture_flow_completed

        capture_flow_completed(
            intent=intent.name if intent else None,
            outcome=outcome,
            failure_reason=failure_reason,
            duration_ms=ms,
            distinct_id=uid,
        )
        return ChatBrainResult(
```

- [ ] **Step 6: Update the two failure call sites**

`brain.py:289` (timeout) — add to the `_finalize(...)` call:

```python
                    usage_cb=usage_cb,
                    outcome="failed",
                    failure_reason="timeout",
                )
```

`brain.py:332` (exception) — the handler already has `exc` in scope:

```python
                usage_cb=usage_cb,
                outcome="failed",
                failure_reason=(
                    "llm_auth_failure"
                    if _is_llm_auth_failure(exc)
                    else type(exc).__name__
                ),
            )
```

Leave the call sites at `brain.py:246` (canned) and `brain.py:301` (normal) untouched — they take the `"ok"` default.

- [ ] **Step 7: Run the brain's own tests**

```bash
.venv-mac/bin/python -m pytest app/domains/ai_engine -q 2>&1 | tail -15
```

Expected: no NEW failures versus the Global Constraints baseline.

- [ ] **Step 8: Stage**

```bash
git add app/core/observability.py app/domains/ai_engine/services/brain.py
```

---

### Task 3: Automatic exception capture → Error Tracking

**Files:**
- Create: `app/core/error_capture.py`
- Modify: `app/core/observability.py` (`capture_exception` becomes the dedupe choke point)
- Modify: `app/core/job_tracing.py` (drop its private flag, defer to the choke point)
- Modify: `app/core/lifespan.py` (attach in `_startup`)
- Test: `app/core/tests/test_error_capture.py` (create)

**Interfaces:**
- Consumes: `capture_exception` from Task 1's module.
- Produces: `attach_error_capture(level: int = logging.WARNING) -> None`, `ErrorTrackingHandler`, module constant `_THROTTLE_SECONDS = 60.0`. Task 4 relies on `capture_exception` deduping internally.

- [ ] **Step 1: Write the failing test**

Create `app/core/tests/test_error_capture.py`:

```python
"""Any logged exception becomes an Error Tracking issue — exactly once, throttled."""

import logging

import pytest

from app.core import error_capture, observability


@pytest.fixture
def captured(monkeypatch):
    seen: list[BaseException] = []
    monkeypatch.setattr(
        observability, "capture_exception",
        lambda exc, **kw: seen.append(exc),
    )
    error_capture._last_sent.clear()
    yield seen
    error_capture._last_sent.clear()


def _log_once(logger_name: str, exc: BaseException) -> logging.LogRecord:
    handler = error_capture.ErrorTrackingHandler(level=logging.WARNING)
    try:
        raise exc
    except BaseException:
        import sys
        record = logging.LogRecord(
            logger_name, logging.ERROR, __file__, 42, "boom", (), sys.exc_info()
        )
    handler.emit(record)
    return record


def test_a_logged_exception_is_reported(captured):
    _log_once("app.jobs", ValueError("kaboom"))
    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)


def test_a_record_without_exc_info_is_ignored(captured):
    handler = error_capture.ErrorTrackingHandler(level=logging.WARNING)
    handler.emit(logging.LogRecord("app", logging.ERROR, __file__, 1, "no exc", (), None))
    assert captured == []


def test_the_same_site_is_throttled(captured):
    """networth_history_service.py:367 logs per SCHEME inside a loop. Without a
    throttle one mfapi outage files thousands of issues from one code line."""
    for _ in range(50):
        _log_once("app.jobs", ValueError("same site"))
    assert len(captured) == 1


def test_a_different_exception_type_is_not_throttled(captured):
    _log_once("app.jobs", ValueError("one"))
    _log_once("app.jobs", KeyError("two"))
    assert len(captured) == 2


def test_a_handler_failure_never_propagates(captured, monkeypatch):
    monkeypatch.setattr(
        observability, "capture_exception",
        lambda exc, **kw: (_ for _ in ()).throw(RuntimeError("posthog down")),
    )
    _log_once("app.jobs", ValueError("kaboom"))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_error_capture.py -v
```

Expected: FAIL, `ModuleNotFoundError: No module named 'app.core.error_capture'`.

- [ ] **Step 3: Create the module**

Create `app/core/error_capture.py`:

```python
"""Forward logged exceptions to PostHog Error Tracking.

74 call sites log an exception with a full stack trace; before this, 2 of them
reached Error Tracking and the rest produced a log line that expires in ~14 days
and cannot be counted or grouped. One handler covers all of them, and every
``except Exception: logger.exception(...)`` written from here on.
"""

from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Per-site rate limit. networth_history_service.py:367 logs inside a per-scheme
# loop, so one upstream outage would file thousands of issues from one line.
_THROTTLE_SECONDS = 60.0
_last_sent: dict[tuple[str, str, str, int], float] = {}

# Guards against a failure inside PostHog's own SDK logging its way back here.
_in_handler: ContextVar[bool] = ContextVar("prozpr_error_capture_active", default=False)


class ErrorTrackingHandler(logging.Handler):
    """Files an Error Tracking issue for any record carrying ``exc_info``."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not record.exc_info:
                return
            exc = record.exc_info[1]
            if exc is None or _in_handler.get():
                return

            key = (record.name, type(exc).__name__, record.pathname, record.lineno)
            now = time.monotonic()
            last = _last_sent.get(key)
            if last is not None and now - last < _THROTTLE_SECONDS:
                return
            _last_sent[key] = now

            token = _in_handler.set(True)
            try:
                from app.core.observability import capture_exception

                capture_exception(exc, properties={"logger": record.name})
            finally:
                _in_handler.reset(token)
        except Exception:  # pragma: no cover - a logging handler must never raise
            pass


def attach_error_capture(level: int = logging.WARNING) -> None:
    """Attach the handler to the root and uvicorn loggers.

    WARNING, not ERROR: the stuck-advisory-lock failure in
    ``mfapi_scheduler.py:214`` is logged at WARNING with ``exc_info=True``, and
    ERROR-only would miss it. The throttle is what makes the wider level safe.

    Same targets as ``attach_otel_logging`` — root and ``uvicorn``, NOT
    ``uvicorn.error``, which propagates and would double-file.
    """
    enabled = os.getenv("POSTHOG_ERROR_CAPTURE_ENABLED", "true").strip().lower()
    if enabled not in ("1", "true", "yes"):
        logger.info("PostHog error capture disabled by POSTHOG_ERROR_CAPTURE_ENABLED.")
        return
    handler = ErrorTrackingHandler(level=level)
    for name in ("", "uvicorn"):
        logging.getLogger(name).addHandler(handler)
    logger.info("PostHog error capture attached at %s.", logging.getLevelName(level))
```

- [ ] **Step 4: Run the test**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_error_capture.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Move dedupe into `capture_exception`**

Three callers can now report the same exception. Make the choke point the only place that decides. In `app/core/observability.py`, add above `capture_exception`:

```python
# Stamped on an exception once it has been filed. The 500 handler, the job
# reporter, and the logging handler can all see the same exception on its way
# out; every one of them SHOULD mark its own span, but the issue is filed once.
_REPORTED_FLAG = "_prozpr_failure_reported"
```

Then in `capture_exception`, after the `if client is None: return`:

```python
    if getattr(exc, _REPORTED_FLAG, False):
        return
```

and after `client.capture_exception(exc, **kwargs)` succeeds:

```python
        try:
            setattr(exc, _REPORTED_FLAG, True)
        except AttributeError:  # pragma: no cover - exotic exceptions
            pass
```

- [ ] **Step 6: Delete the duplicate flag from `job_tracing.py`**

In `app/core/job_tracing.py`, delete the `_REPORTED_FLAG` constant and its comment block, and simplify `report_job_failure`'s body to:

```python
    from app.core.observability import capture_exception

    try:
        span = trace.get_current_span()
        if span is not None and span.is_recording():
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        capture_exception(exc, properties={"job": job, **attributes})
    except Exception:  # pragma: no cover - reporting must never raise
        logger.warning("job failure reporting failed for %s", job, exc_info=True)
```

Span marking stays before the dedupe, so nested run/phase spans all go red while the issue files once — the existing behaviour, now with one owner.

- [ ] **Step 7: Attach at startup**

In `app/core/lifespan.py`'s `_startup()`, immediately after the `init_posthog()` call:

```python
    from app.core.error_capture import attach_error_capture

    attach_error_capture()
```

In the lifespan, not module scope: importing `app.main` must not start filing issues.

- [ ] **Step 8: Run the whole core suite**

```bash
.venv-mac/bin/python -m pytest app/core -q 2>&1 | tail -15
```

Expected: all pass. `test_job_tracing.py::test_nested_spans_all_mark_error_but_file_one_issue` must still pass — it is the regression guard for Step 6.

- [ ] **Step 9: Stage**

```bash
git add app/core/error_capture.py app/core/observability.py app/core/job_tracing.py app/core/lifespan.py
```

---

### Task 4: `job_completed`, emitted by `@traced_job` itself

The decorator, not each job — a future job gets full coverage from one line, with no second step to forget.

**Files:**
- Modify: `app/core/observability.py` (add `capture_job_completed`)
- Modify: `app/core/job_tracing.py` (state ContextVar, `record_job_counts`, decorator emits)
- Test: `app/core/tests/test_job_tracing.py` (extend)

**Interfaces:**
- Consumes: `capture_exception` deduping (Task 3).
- Produces: `capture_job_completed(*, job, outcome, failure_reason, duration_ms, counts) -> None`; `record_job_counts(**counts) -> None`. Task 5 calls `record_job_counts`.

**Design note.** The schedulers catch their own exceptions, so a failed run returns normally and the decorator's `except` never fires. `report_job_failure` therefore also writes the outcome into the shared state — that is what makes `outcome` correct for both the raising and the caught-and-reported shapes.

- [ ] **Step 1: Write the failing tests**

Append to `app/core/tests/test_job_tracing.py`. The file already has
`from app.core import job_tracing, observability`, and `pyproject.toml` sets
`asyncio_mode = "auto"`, so `async def test_*` runs natively — no `asyncio.run`.

```python
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


async def test_traced_job_emits_failed_when_the_job_raises(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(job_tracing, "capture_job_completed", lambda **kw: sent.append(kw))

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
    still flip the outcome, or a crashed job reports success — which is exactly
    what happened to the mfapi NUMERIC-overflow crash."""
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
```

- [ ] **Step 2: Run and watch them fail**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_job_tracing.py -v
```

Expected: the 3 new tests FAIL on `capture_job_completed` not existing. Pre-existing tests in the file still pass.

- [ ] **Step 3: Add the emitter**

In `app/core/observability.py`, after `capture_flow_completed`:

```python
def capture_job_completed(
    *,
    job: str,
    outcome: str,
    failure_reason: str | None,
    duration_ms: float,
    counts: dict[str, object] | None = None,
) -> None:
    """Record one background job run as a durable event.

    An event, not a span: a job that stops running entirely emits no error, no
    span and no log, so it is detectable only as the ABSENCE of an expected
    event — and absence can only be charted against a durable signal.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "job_completed",
            distinct_id="backend",
            properties={
                "job": job,
                "outcome": outcome,
                "failure_reason": failure_reason,
                "duration_ms": round(duration_ms, 2),
                "$process_person_profile": False,
                **(counts or {}),
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass
```

- [ ] **Step 4: Add run state to `job_tracing.py`**

Add near the top of `app/core/job_tracing.py` (after `logger`):

```python
import time
from contextvars import ContextVar

# Module-level, not function-local like the other observability imports here:
# the decorator calls it, and tests monkeypatch `job_tracing.capture_job_completed`,
# which only works if the name is bound on this module. No cycle — observability
# does not import job_tracing.
from app.core.observability import capture_job_completed

# Per-run state for the enclosing @traced_job. A ContextVar, so concurrent jobs
# in different tasks never see each other's counts.
_job_state: ContextVar[dict | None] = ContextVar("prozpr_job_state", default=None)


def record_job_counts(**counts: object) -> None:
    """Attach per-run numbers to the enclosing job's ``job_completed`` event.

    A job that "succeeds" while inserting zero rows is broken; the counts are
    what make that visible. No-op outside a @traced_job.
    """
    state = _job_state.get()
    if state is not None:
        state["counts"].update(counts)
```

In `report_job_failure`, after the span is marked and before `capture_exception`:

```python
        state = _job_state.get()
        if state is not None:
            state["outcome"] = "failed"
            state["failure_reason"] = type(exc).__name__
```

- [ ] **Step 5: Make the decorator emit**

Replace `traced_job`'s `wrapper` in `app/core/job_tracing.py`:

```python
    def decorate(func: _F) -> _F:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> object:
            state: dict = {"outcome": "ok", "failure_reason": None, "counts": {}}
            token = _job_state.set(state)
            t0 = time.monotonic()
            try:
                with job_span(name, **attributes):
                    return await func(*args, **kwargs)
            except BaseException as exc:
                state["outcome"] = "failed"
                state["failure_reason"] = type(exc).__name__
                raise
            finally:
                _job_state.reset(token)
                capture_job_completed(
                    job=name,
                    outcome=state["outcome"],
                    failure_reason=state["failure_reason"],
                    duration_ms=(time.monotonic() - t0) * 1000,
                    counts=state["counts"],
                )

        return wrapper  # type: ignore[return-value]
```

- [ ] **Step 6: Run the tests**

```bash
.venv-mac/bin/python -m pytest app/core/tests/test_job_tracing.py -v
```

Expected: all pass, old and new. If the monkeypatch of `capture_job_completed` does not take effect, the import in Step 4 must be module-level (`from ... import capture_job_completed`) and referenced unqualified — `monkeypatch.setattr(job_tracing, ...)` rebinds the module attribute.

- [ ] **Step 7: Stage**

```bash
git add app/core/observability.py app/core/job_tracing.py
```

---

### Task 5: Instrument `run_daily_networth_job`

The third scheduled job — the only one that touches every user's money — with no tracing at all. Task 3 already covers its per-user `logger.exception`, so this task only adds the run-level signal.

**Files:**
- Modify: `app/domains/portfolio/services/networth_history_service.py:756`
- Test: `app/domains/portfolio/tests/test_networth_job_tracing.py` (create)

**Interfaces:**
- Consumes: `traced_job`, `record_job_counts` (Task 4).
- Produces: `job_completed` with `job="networth.daily_job"` and counts `users_total`, `users_refreshed`.

- [ ] **Step 1: Write the failing test**

Create `app/domains/portfolio/tests/test_networth_job_tracing.py`:

```python
"""The net-worth job must report a run outcome and its per-user counts."""

from app.domains.portfolio.services import networth_history_service as svc


def test_the_daily_job_is_traced():
    """@traced_job wraps with functools.wraps, so the decorated function keeps
    its name but gains a __wrapped__ attribute."""
    assert hasattr(svc.run_daily_networth_job, "__wrapped__"), (
        "run_daily_networth_job is not decorated with @traced_job"
    )
```

- [ ] **Step 2: Run and watch it fail**

```bash
.venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_networth_job_tracing.py -v
```

Expected: FAIL on the assertion message.

- [ ] **Step 3: Decorate the job and record counts**

In `app/domains/portfolio/services/networth_history_service.py`, add to the imports:

```python
from app.core.job_tracing import record_job_counts, traced_job
```

Decorate the job at line 756:

```python
@traced_job("networth.daily_job")
async def run_daily_networth_job() -> None:
```

And immediately after the existing `logger.info("networth daily job: refreshed ...")` call (~line 800):

```python
                record_job_counts(
                    users_total=len(user_ids),
                    users_refreshed=done,
                    users_failed=len(user_ids) - done,
                )
```

- [ ] **Step 4: Run the test and the portfolio suite**

```bash
.venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_networth_job_tracing.py -v
.venv-mac/bin/python -m pytest app/domains/portfolio -q 2>&1 | tail -10
```

Expected: the new test passes; no new failures in the domain.

- [ ] **Step 5: Extend the preflight harness**

Unit tests prove the emitters are called. Only the preflight proves events actually
*arrive* at PostHog. `scratchpad/preflight_telemetry.py` already refuses to run unless
`DEPLOY_ENV` starts with `preflight`, and deliberately skips the lifespan so it never
touches the live RDS. Add to it:

```python
from app.core.observability import capture_flow_completed, capture_job_completed

capture_flow_completed(
    intent="rebalancing", outcome="failed", failure_reason="timeout",
    duration_ms=1234.0, distinct_id="preflight-user",
)
capture_job_completed(
    job="preflight.fake_job", outcome="failed", failure_reason="RuntimeError",
    duration_ms=4567.0, counts={"rows": 3},
)
```

- [ ] **Step 6: Run the preflight and confirm arrival**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && \
  PYTHONPATH=. DEPLOY_ENV=preflight-4 .venv-mac/bin/python scratchpad/preflight_telemetry.py
```

Then query through the PostHog connector, allowing 1–2 minutes for ingestion lag —
an empty first result is normal, re-query before concluding anything failed:

```sql
SELECT event, properties.outcome AS outcome, properties.failure_reason AS reason
FROM events
WHERE event IN ('flow_completed', 'job_completed')
  AND properties.environment = 'preflight-4'
  AND timestamp >= now() - INTERVAL 1 HOUR
```

Expected: both events present, `outcome = 'failed'`, reasons intact.

- [ ] **Step 7: Stage**

```bash
git add app/domains/portfolio/services/networth_history_service.py app/domains/portfolio/tests/test_networth_job_tracing.py
```

---

### Task 6: Comment-trimming pass

Measured baseline: `app/` averages 14.8% comment + docstring lines. The five observability files run 32–47%. Target ~20%.

**Files:**
- Modify: `app/core/otel.py`, `app/core/observability.py`, `app/core/job_tracing.py`, `app/core/exceptions.py`, `app/core/log_scrubber.py`
- Tool: `/private/tmp/claude-502/-Users-Amoul-Documents-AILAX-AI-Financial-advisor-ailax-Prozpr-Backend/d3ebcdd8-e686-47da-a015-eab340469bff/scratchpad/comment_density.py`

**Interfaces:** none — comments only. No code, signature, or behaviour changes.

- [ ] **Step 1: Record the starting numbers**

```bash
.venv-mac/bin/python /private/tmp/claude-502/-Users-Amoul-Documents-AILAX-AI-Financial-advisor-ailax-Prozpr-Backend/d3ebcdd8-e686-47da-a015-eab340469bff/scratchpad/comment_density.py
```

- [ ] **Step 2: Trim, applying the keep/delete rule**

**Keep** only comments that cost a debugging session if deleted:
- Landmines — "looks wrong, is deliberate, changing it silently breaks X". Examples that MUST survive: the `instrument_app()` module-scope requirement, the `record_exception=False` note in `job_span`, the `uvicorn` vs `uvicorn.error` attach note, the `scope[_START_NS_KEY]` timing note.
- A number with a source — *why* `kill_timeout` is 8000, *why* the phase size is 150.
- A documented dead end.

**Delete:**
- Anything restating the line below it.
- Multi-paragraph background already recorded in `app/core/CLAUDE.md` — that duplication is the bulk of the excess, and CLAUDE.md is the better home because a new session reads it first.
- Module docstrings longer than ~8 lines: keep the first paragraph, move the rest to CLAUDE.md if it is not already there.

- [ ] **Step 3: Verify the numbers landed near 20%**

```bash
.venv-mac/bin/python /private/tmp/claude-502/-Users-Amoul-Documents-AILAX-AI-Financial-advisor-ailax-Prozpr-Backend/d3ebcdd8-e686-47da-a015-eab340469bff/scratchpad/comment_density.py
```

Expected: all five files between roughly 18% and 24%.

- [ ] **Step 4: Prove nothing changed but comments**

```bash
.venv-mac/bin/python -m pytest app/core -q 2>&1 | tail -5
git diff --stat app/core/
```

Expected: same test results as Task 3 Step 8. Confirm by reading the diff that no executable line moved.

- [ ] **Step 5: Stage**

```bash
git add app/core/otel.py app/core/observability.py app/core/job_tracing.py app/core/exceptions.py app/core/log_scrubber.py
```

---

### Task 7: Dashboards

Built through the PostHog MCP connector (project 484367). `flow_completed` and `job_completed` tiles read zero until the branch is deployed — that is expected; they populate on their own.

**Files:** none in the repo. PostHog entities only.

**Interfaces:**
- Consumes: `domain` (Task 1), `flow_completed` (Task 2), `job_completed` (Task 4).

- [ ] **Step 1: Check the test-account filter before touching anything**

Several existing insights set `filterTestAccounts: true`, and backend events all use `distinct_id = "backend"`. If the project's test-account filter happens to match that identity, every rewritten tile reads zero for a brand-new reason — and it would look identical to "the deploy hasn't happened yet".

Establish the ground truth first with raw SQL, which ignores the flag entirely:

```sql
SELECT count() AS raw_total
FROM events
WHERE event = 'http_request' AND timestamp >= now() - INTERVAL 7 DAY
```

Then build **one** throwaway TrendsQuery insight on `http_request` with `filterTestAccounts: true` and run it. If its total matches `raw_total`, the flag is harmless — proceed and keep it. If it returns 0, set `filterTestAccounts: false` on every tile in Steps 3–7. Delete the throwaway insight either way.

- [ ] **Step 2: Fix the one tile that queries the renamed property**

Insight `10446384` ("Server errors by service (30d)") on dashboard `1904646`. Change its HogQL from `properties.service` to `properties.domain`:

```sql
SELECT coalesce(properties.domain, '(unknown)') AS domain, count() AS errors
FROM events
WHERE event = '$exception'
  AND properties.$lib = 'posthog-python'
  AND timestamp >= now() - INTERVAL 30 DAY
GROUP BY domain
ORDER BY errors DESC
```

- [ ] **Step 3: Rewrite the 7 salvageable 4xx tiles onto `http_request`**

On dashboard `1904647`, swap the source event on each. The pattern for every one is: `event = 'http_client_error'` becomes `event = 'http_request' AND properties.status_class = '4xx'`. Worked example for insight `10446279` ("Client errors by status code"):

```sql
SELECT toString(properties.status_code) AS status_code, count() AS errors
FROM events
WHERE event = 'http_request'
  AND properties.status_class = '4xx'
  AND timestamp >= now() - INTERVAL 30 DAY
GROUP BY status_code
ORDER BY errors DESC
```

Apply the same swap to: `10446276` (over time), `10446280` (by endpoint), `10488137` (endpoint × status), `10488132` (by method), `10488190` (week-over-week), `10488177` (auth failures 401/403).

- [ ] **Step 4: Rebuild the rate tile with a real denominator**

Insight `10488156` used `$pageview` as a proxy because the old event only fired on errors. `http_request` records every request, so replace it entirely:

```sql
SELECT toStartOfDay(timestamp) AS day,
       countIf(properties.status_class = '4xx') AS errors,
       count() AS requests,
       round(countIf(properties.status_class = '4xx') / count() * 100, 2) AS error_pct
FROM events
WHERE event = 'http_request' AND timestamp >= now() - INTERVAL 30 DAY
GROUP BY day ORDER BY day
```

- [ ] **Step 5: Delete the two structurally broken tiles**

Insights `10488100` ("Client errors by user") and `10488270` ("New vs recurring"). Both do per-user math over `distinct_id`, which `capture_http_request` hardcodes to `"backend"`. Not repairable — one identity cannot have a per-user distribution.

- [ ] **Step 6: Create "Is Prozpr working?"**

New dashboard, 7 tiles on `flow_completed`. Success rate:

```sql
SELECT round(countIf(properties.outcome = 'ok') / count() * 100, 1) AS success_pct
FROM events
WHERE event = 'flow_completed' AND timestamp >= now() - INTERVAL 7 DAY
```

Failures by intent, then by reason — the what-then-why pair:

```sql
SELECT properties.intent AS intent, count() AS failures
FROM events
WHERE event = 'flow_completed' AND properties.outcome = 'failed'
  AND timestamp >= now() - INTERVAL 7 DAY
GROUP BY intent ORDER BY failures DESC
```

```sql
SELECT properties.failure_reason AS reason, count() AS failures
FROM events
WHERE event = 'flow_completed' AND properties.outcome = 'failed'
  AND timestamp >= now() - INTERVAL 7 DAY
GROUP BY reason ORDER BY failures DESC
```

Plus: outcome over time (daily, split by outcome), latency p95 by intent (`quantile(0.95)(toFloat(properties.duration_ms))`), unique users affected (`uniq(distinct_id)` where outcome = 'failed'), and daily volume. Add a text tile linking to dashboard `1904596` for LLM cost — do not rebuild those tiles.

- [ ] **Step 7: Add the job and infra tiles to dashboard `1904646`**

The absence check — the tile nothing else in the stack can give you:

```sql
SELECT properties.job AS job, properties.outcome AS outcome, count() AS runs
FROM events
WHERE event = 'job_completed' AND timestamp >= now() - INTERVAL 7 DAY
GROUP BY job, outcome ORDER BY job, outcome
```

mfapi runs 3×/day, so 7 days should read 21. A lower number means it silently stopped.

Infra, respecting both metric caveats — average across the per-core dimension, and `argMax` for cumulative sums:

```sql
SELECT toStartOfMinute(timestamp) AS minute, avg(value) AS cpu_pct
FROM posthog.metrics
WHERE metric_name = 'system.cpu.utilization'
  AND service_name = 'prozpr-backend'
  AND timestamp >= now() - INTERVAL 6 HOUR
GROUP BY minute ORDER BY minute
```

```sql
SELECT toStartOfMinute(timestamp) AS minute, argMax(value, timestamp) AS bytes
FROM posthog.metrics
WHERE metric_name = 'process.memory.usage'
  AND service_name = 'prozpr-backend'
  AND timestamp >= now() - INTERVAL 6 HOUR
GROUP BY minute ORDER BY minute
```

Also add: 5xx rate, API latency p95 by endpoint, job duration trend, and request volume — all from `http_request` and `job_completed`.

- [ ] **Step 8: Verify every tile returns rows or a known-empty reason**

Run each dashboard's insights and confirm each either returns data or is empty **only** because its source event awaits deploy. Any other empty tile is a bug in the query.

---

## Notes for the implementer

- Tasks 1–6 are code and can run back to back. Task 7 needs no deploy to start, but its `flow_completed` / `job_completed` tiles stay empty until `Backend_Observablity` reaches `main`.
- Task 1 is a standalone bug fix on already-deployed code. It can ship with the pending branch ahead of everything else.
- Nothing here is committed. Everything is staged for the user.
