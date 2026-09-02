"""App lifespan — startup + shutdown hooks.

One public entry point — the ``lifespan`` async context manager that
``app.main`` hands to ``FastAPI(lifespan=lifespan)``. The flow is:

    startup:
        1. log which DB engine + host we're pointed at
        2. (unless SKIP_STARTUP_DB_DDL) create tables + apply pg schema patches
        3. (if MFAPI_SCHEDULER_ENABLED) start the mfapi.in NAV polling job
        4. (if POSTHOG_API_KEY) build the PostHog LLM observability client
    shutdown:
        5. stop the scheduler, flush PostHog, dispose the SQLAlchemy engine

Each step is independent — failures log and continue so a flaky DB or
scheduler doesn't keep the process from coming up far enough to serve
``/health``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.database import (
    apply_postgres_schema_patches,
    create_all_tables,
    dispose_engine,
)
from app.core.error_capture import attach_error_capture
from app.core.observability import init_posthog, shutdown_posthog
from app.core.otel import init_metrics, shutdown_otel
from app.domains.mutual_funds.services.mfapi_scheduler import (
    shutdown_scheduler,
    start_scheduler,
)
from app.domains.benchmarks.services.benchmark_scheduler import (
    shutdown_benchmark_scheduler,
    start_benchmark_scheduler,
)

logger = logging.getLogger(__name__)

_BANNER = "=" * 60


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan hook — runs ``_startup`` then yields, then ``_shutdown``."""
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


async def _startup() -> None:
    # First: anything that fails below this line should be reportable.
    init_posthog()
    # Turns every logged exception into an Error Tracking issue. In the lifespan
    # rather than at module scope so importing app.main never starts filing.
    attach_error_capture()
    # Metrics start here, not at module scope like the tracer: this begins a
    # background collection loop, and importing app.main (tests, tooling) must
    # not silently start shipping gauges. shutdown_otel() flushes it.
    init_metrics()

    logger.info(_BANNER)
    logger.info("Starting Ask PI API v2.0")
    logger.info(_BANNER)

    _log_db_engine_info()
    await _initialize_database()
    _start_schedulers()

    logger.info("Server ready! Docs at /docs")
    logger.info(_BANNER)


def _log_db_engine_info() -> None:
    """Print which DB engine + host we're pointed at."""
    try:
        parsed = make_url(get_settings().get_database_url())
    except Exception as exc:
        logger.error("Cannot parse DATABASE_URL: %s", exc)
        return

    if parsed.drivername.startswith("postgresql"):
        logger.info(
            "Database engine: postgresql (host=%s, db=%s). "
            "Run `alembic upgrade head` on RDS for migrations.",
            parsed.host or "?",
            parsed.database or "?",
        )
    elif parsed.drivername.startswith("sqlite"):
        logger.warning("Database engine: sqlite (ALLOW_SQLITE dev mode only)")


async def _initialize_database() -> None:
    """Create tables (dev convenience) and apply postgres-only schema patches.

    Honours ``SKIP_STARTUP_DB_DDL`` — in shared / prod environments we expect
    ``alembic upgrade head`` to own the schema and skip this entirely.
    """
    settings = get_settings()
    if settings.skip_startup_db_ddl():
        logger.info(
            "Skipping startup DB DDL (SKIP_STARTUP_DB_DDL=true). "
            "Ensure schema exists (e.g. alembic upgrade head on RDS)."
        )
        return

    try:
        await create_all_tables()
    except Exception as exc:
        logger.error("Database setup failed: %s", exc)
        return

    try:
        await apply_postgres_schema_patches()
    except Exception as exc:
        # Patches are postgres-only column fix-ups; safe to skip on sqlite or
        # when the DB role lacks ALTER privileges. Log and move on.
        logger.warning(
            "Postgres schema patches failed "
            "(check DB permissions / table chat_ai_module_runs): %s",
            exc,
        )

    logger.info("Database tables ready (create_all).")


def _start_schedulers() -> None:
    """Start background schedulers, each gated by its own env flag.

    - mfapi.in NAV polling (``MFAPI_SCHEDULER_ENABLED``)
    - thrice-daily benchmark EOD refresh (``BENCHMARK_SCHEDULER_ENABLED``)
    - daily DPDP erasure purge + retention pass (``PRIVACY_SCHEDULER_ENABLED``)
    - Value Research mirror refresh (``VR_SYNC_ENABLED``, default **off**)
    """
    if not get_settings().mfapi_scheduler_enabled():
        logger.info("mfapi scheduler disabled (MFAPI_SCHEDULER_ENABLED is false)")
    else:
        try:
            start_scheduler()
        except Exception as exc:
            logger.warning("mfapi scheduler failed to start: %s", exc)

    if not get_settings().benchmark_scheduler_enabled():
        logger.info(
            "benchmark scheduler disabled (BENCHMARK_SCHEDULER_ENABLED is false)"
        )
    else:
        try:
            start_benchmark_scheduler()
        except Exception as exc:
            logger.warning("benchmark scheduler failed to start: %s", exc)

    if not get_settings().privacy_scheduler_enabled():
        logger.info("privacy scheduler disabled (PRIVACY_SCHEDULER_ENABLED is false)")
    else:
        try:
            from app.domains.privacy.services.privacy_scheduler import (
                start_privacy_scheduler,
            )

            start_privacy_scheduler()
        except Exception as exc:
            logger.warning("privacy scheduler failed to start: %s", exc)

    # Imported lazily: the VR domain builds 33 Core Tables at import time, and
    # a process that never enables the mirror should not pay for that.
    try:
        from app.domains.vr_data.scheduler import start_vr_scheduler

        start_vr_scheduler()
    except Exception as exc:
        logger.warning("vr scheduler failed to start: %s", exc)


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


async def _shutdown() -> None:
    logger.info("Shutting down Ask PI API...")
    await shutdown_scheduler()
    await shutdown_benchmark_scheduler()
    try:
        from app.domains.privacy.services.privacy_scheduler import (
            shutdown_privacy_scheduler,
        )

        await shutdown_privacy_scheduler()
    except Exception:  # pragma: no cover - shutdown must not raise
        logger.warning("privacy scheduler shutdown failed.", exc_info=True)
    try:
        from app.domains.vr_data.scheduler import shutdown_vr_scheduler

        await shutdown_vr_scheduler()
    except Exception:  # pragma: no cover - shutdown must not raise
        logger.warning("vr scheduler shutdown failed.", exc_info=True)
    # Flush buffered LLM events before the process goes away, or the last turns'
    # traces are lost.
    shutdown_posthog()
    # Same for OTLP spans and logs. PM2 SIGKILLs ~1.6s after SIGINT by default, so
    # ecosystem.config.cjs raises kill_timeout to give these a chance to complete.
    shutdown_otel()
    await dispose_engine()
    logger.info("Shutdown complete")
