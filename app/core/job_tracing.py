"""Spans, events and error reporting for background jobs.

ASGI instrumentation only creates a span because *a request arrived*, so
schedulers were a black box: an 800-second mfapi sweep produced no duration, no
success record, and no Error Tracking issue.

Two entry points, separate because the schedulers catch their own exceptions
rather than letting them propagate: ``job_span`` for work that raises out of the
block, ``report_job_failure`` for one the caller already caught.

``suppress_instrumentation`` is re-exported so the reason lives in one place:
SQLAlchemy and httpx are instrumented globally (``app/main.py``), which is right
inside a request and ruinous in a bulk loop — the ~8k-scheme mfapi sweep would
emit ~25k spans into one unreadable trace. Wrap the per-item loop; keep the
run/phase spans outside it.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.instrumentation.utils import (  # noqa: F401  (re-export)
    suppress_instrumentation,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

# Module-level, unlike the function-local observability imports below: the
# decorator calls it, and tests monkeypatch `job_tracing.capture_job_completed`,
# which only works if the name is bound here. No cycle — observability does not
# import this module.
from app.core.observability import capture_job_completed

logger = logging.getLogger(__name__)

# Per-run state for the enclosing @traced_job. A ContextVar, not a global, so
# concurrent jobs in different asyncio tasks never see each other's numbers.
_job_state: ContextVar[dict | None] = ContextVar("prozpr_job_state", default=None)


def record_job_counts(**counts: object) -> None:
    """Attach per-run numbers to the enclosing job's ``job_completed`` event.

    A job that "succeeds" while inserting zero rows is broken; these counts are
    what make that visible. No-op outside a ``@traced_job``.
    """
    state = _job_state.get()
    if state is not None:
        state["counts"].update(counts)

_NOOP_TRACER = trace.NoOpTracer()


def _tracer() -> trace.Tracer:
    """Real tracer when OTel is on, a no-op one otherwise.

    Resolved per call, not at import: caching the provider would pin whichever
    one existed when this module was first imported.
    """
    from app.core.otel import get_tracer_provider

    provider = get_tracer_provider()
    if provider is None:
        return _NOOP_TRACER
    return provider.get_tracer(__name__)


def report_job_failure(exc: BaseException, *, job: str, **attributes: object) -> None:
    """Mark the active span failed and report ``exc`` to PostHog Error Tracking.

    For the scheduler shape where the exception is caught and logged, not raised.

    Job spans nest (run > phase), so one failure crosses several of these. Every
    span it touches goes ERROR — that is what makes the trace readable — while
    ``capture_exception`` dedupes so the issue files once.
    """
    from app.core.observability import capture_exception

    try:
        span = trace.get_current_span()
        if span is not None and span.is_recording():
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        # The schedulers catch their own exceptions, so the decorator's `except`
        # never fires and the run would otherwise report success.
        state = _job_state.get()
        if state is not None:
            state["outcome"] = "failed"
            state["failure_reason"] = type(exc).__name__
        capture_exception(exc, properties={"job": job, **attributes})
    except Exception:  # pragma: no cover - reporting must never raise
        logger.warning("job failure reporting failed for %s", job, exc_info=True)


@contextlib.contextmanager
def job_span(name: str, **attributes: object) -> Iterator[Span]:
    """Trace one background job run, or one phase of it.

    An exception leaving the block marks the span ERROR, files it, and re-raises
    unchanged — observing a failure must not swallow it.
    """
    # record_exception/set_status_on_exception OFF: this block owns the error
    # path, and OTel's defaults would record the same exception twice.
    with _tracer().start_as_current_span(
        name,
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)  # type: ignore[arg-type]
        try:
            yield span
        except BaseException as exc:
            report_job_failure(exc, job=name, **attributes)
            raise


_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def traced_job(name: str, **attributes: object) -> Callable[[_F], _F]:
    """Decorator form of ``job_span``, plus the ``job_completed`` event.

    Emitting the event here rather than in each job is what makes a new job fully
    tracked by adding one line, with no second step to forget.
    """

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

    return decorate
