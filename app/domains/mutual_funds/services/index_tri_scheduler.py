"""Independent daily scheduler for the NSE Nifty 50 TRI refresh.

Separate AsyncIOScheduler from ``mfapi_scheduler`` (different source + cadence).
Runs ``run_tri_refresh_job`` at 20:30 IST (after NSE publishes EOD). First run
against an empty table backfills full history. Serialized across workers via a
Postgres advisory lock inside the job. Started/stopped from ``app.core.lifespan``
and gated by ``INDEX_TRI_SCHEDULER_ENABLED``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.domains.mutual_funds.services.index_tri_service import run_tri_refresh_job

logger = logging.getLogger(__name__)

INDEX_TRI_TIMEZONE = "Asia/Kolkata"
INDEX_TRI_DAILY_HOUR = 20
INDEX_TRI_DAILY_MINUTE = 30

_tri_scheduler: Optional[Any] = None


def start_tri_scheduler() -> Optional[Any]:
    global _tri_scheduler
    if _tri_scheduler is not None:
        return _tri_scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        logger.warning(
            "apscheduler not installed; TRI daily refresh disabled. (%s)", exc
        )
        return None

    sched = AsyncIOScheduler(
        timezone=INDEX_TRI_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    sched.add_job(
        run_tri_refresh_job,
        trigger=CronTrigger(
            hour=INDEX_TRI_DAILY_HOUR,
            minute=INDEX_TRI_DAILY_MINUTE,
            second=0,
            timezone=INDEX_TRI_TIMEZONE,
        ),
        id="index_tri_daily_refresh",
        name="Daily Nifty 50 TRI refresh (20:30 IST)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    sched.start()
    _tri_scheduler = sched

    for job in sched.get_jobs():
        logger.info(
            "TRI scheduler: [%s] %s — next run: %s", job.id, job.name, job.next_run_time
        )
    return sched


async def shutdown_tri_scheduler() -> None:
    global _tri_scheduler
    if _tri_scheduler is None:
        return
    try:
        _tri_scheduler.shutdown(wait=False)
        logger.info("TRI scheduler shut down cleanly")
    except Exception:
        logger.exception("TRI scheduler shutdown failed")
    finally:
        _tri_scheduler = None
