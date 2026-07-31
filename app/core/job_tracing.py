"""Spans and error reporting for background jobs — the half HTTP instrumentation cannot see.

The ASGI instrumentation only ever creates a span because *a request arrived*.
Schedulers are not requests, so before this module they were a black box: an
800-second mfapi sweep produced no duration, no success record, no tree, and
never became an Error Tracking issue — ``capture_exception`` is wired only into
the HTTP handler in ``app/core/exceptions.py``, which a job never reaches.

Two tools, deliberately separate because the schedulers already catch their own
exceptions rather than letting them propagate:

- ``job_span`` for work that raises out of the block.
- ``report_job_failure`` for an exception the caller has already caught.

``suppress_instrumentation`` is re-exported here so the reason lives in one
place: SQLAlchemy and httpx are instrumented globally (``app/main.py``), which
is right inside a request and ruinous inside a bulk loop — the mfapi sweep
touches ~8k schemes, so one run would emit ~25k spans in a single trace no
viewer can render and no human can read. Wrap the per-item loop, keep the
run/phase spans outside it.
"""

from __future__ import annotations

import contextlib
import functools
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.instrumentation.utils import (  # noqa: F401  (re-export)
    suppress_instrumentation,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

_NOOP_TRACER = trace.NoOpTracer()


def _tracer() -> trace.Tracer:
    """Real tracer when OTel is on, a no-op one otherwise.

    Resolved per call rather than at import: ``init_otel()`` runs at the top of
    ``app/main.py``, but schedulers are imported from the lifespan, and a module
    that cached the provider at import time would pin whichever one existed then.
    """
    from app.core.otel import get_tracer_provider

    provider = get_tracer_provider()
    if provider is None:
        return _NOOP_TRACER
    return provider.get_tracer(__name__)


# Stamped on an exception once it has been sent to Error Tracking. Job spans
# nest (run > phase), so one failure passes through several ``job_span`` blocks
# on its way out. Every span it crosses SHOULD be marked ERROR — that is what
# makes the trace readable — but the issue must be filed exactly once.
_REPORTED_FLAG = "_prozpr_job_failure_reported"


def report_job_failure(exc: BaseException, *, job: str, **attributes: object) -> None:
    """Mark the active span failed and report ``exc`` to PostHog Error Tracking.

    For the common scheduler shape where the exception is caught and logged
    rather than raised. Best-effort: telemetry must never turn a handled job
    failure into an unhandled one.
    """
    from app.core.observability import capture_exception

    try:
        span = trace.get_current_span()
        if span is not None and span.is_recording():
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        if getattr(exc, _REPORTED_FLAG, False):
            return
        capture_exception(exc, properties={"job": job, **attributes})
        try:
            setattr(exc, _REPORTED_FLAG, True)
        except AttributeError:  # pragma: no cover - exotic exceptions
            pass
    except Exception:  # pragma: no cover - reporting must never raise
        logger.warning("job failure reporting failed for %s", job, exc_info=True)


@contextlib.contextmanager
def job_span(name: str, **attributes: object) -> Iterator[Span]:
    """Trace one background job run, or one phase of it.

    On an exception leaving the block the span is marked ERROR, the exception is
    recorded on it, and it is reported to Error Tracking — then re-raised
    unchanged. Observing a failure must not swallow it.
    """
    # record_exception/set_status_on_exception OFF: this block owns the error
    # path, and leaving OTel's defaults on records the same exception twice —
    # once by the SDK, once by report_job_failure.
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
    """Decorator form of ``job_span`` for an async job entry point.

    Exists so wrapping an existing scheduler costs one line instead of
    re-indenting its whole body — a diff that would collide with anyone else
    editing the same job.
    """

    def decorate(func: _F) -> _F:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> object:
            with job_span(name, **attributes):
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate
