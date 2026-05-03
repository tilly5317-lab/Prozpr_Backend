"""Daily scheduler for the mfapi.in MF master + NAV ingestion.

Cron-fires at 00:00 IST (the daily refresh window — mfapi mirrors AMFI which
publishes ~22:00 IST). Runs an incremental ingest on its own ``AsyncSession``
and serializes against concurrent uvicorn workers via a Postgres advisory lock
so only one process performs the run.

Started/stopped from ``app.main`` lifespan and gated by
``MFAPI_SCHEDULER_ENABLED`` so test runs do not spin it up.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from app.services.mf.mfapi_ingest_service import IngestMode, MfapiIngestError, ingest_mfapi

logger = logging.getLogger(__name__)


MFAPI_LOCK_KEY = 7421100  # constant chosen once; pg_advisory_lock namespace
MFAPI_TIMEZONE = "Asia/Kolkata"

_scheduler: Optional[Any] = None


async def run_daily_mfapi_job() -> None:
    """One scheduler firing. Skips silently if another worker holds the lock."""
    from app.database import _get_session_factory

    factory = _get_session_factory()
    async with factory() as db:
        got_lock = (
            await db.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": MFAPI_LOCK_KEY}
            )
        ).scalar()
        if not got_lock:
            logger.info("mfapi daily job: lock held by another worker; skipping")
            return
        try:
            await ingest_mfapi(db, mode=IngestMode.INCREMENTAL)
        except MfapiIngestError as exc:
            logger.error("mfapi daily job failed: %s", exc)
        except Exception:
            logger.exception("mfapi daily job crashed")
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": MFAPI_LOCK_KEY}
            )


def start_scheduler() -> Optional[Any]:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        logger.warning(
            "apscheduler not installed; mfapi daily refresh disabled. "
            "pip install -r requirements.txt to enable. (%s)",
            exc,
        )
        return None
    sched = AsyncIOScheduler(timezone=MFAPI_TIMEZONE)
    sched.add_job(
        run_daily_mfapi_job,
        trigger=CronTrigger(hour=0, minute=0, timezone=MFAPI_TIMEZONE),
        id="mfapi_daily_refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    sched.start()
    _scheduler = sched
    next_run = sched.get_job("mfapi_daily_refresh").next_run_time
    logger.info("mfapi scheduler started; next run %s", next_run)
    return sched


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("mfapi scheduler shutdown failed")
    finally:
        _scheduler = None
