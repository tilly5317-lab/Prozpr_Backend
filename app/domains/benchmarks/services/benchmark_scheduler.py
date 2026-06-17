"""Thrice-daily scheduler for the benchmark EOD refresh.

Independent ``AsyncIOScheduler`` (separate source + cadence from the mfapi
scheduler). Fires ``run_benchmark_refresh_job`` three times a day at the IST
hours in ``BENCHMARK_REFRESH_HOURS`` — each run re-fetches a short trailing
window and upserts the latest EOD value so the day's close is captured reliably
even if NSE publishes late. The first run against an empty table bootstraps the
index with a multi-year backfill. Serialized across workers via a Postgres
advisory lock inside the job. Started/stopped from ``app.core.lifespan`` and
gated by ``BENCHMARK_SCHEDULER_ENABLED``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.domains.benchmarks.services.benchmark_data_service import (
    run_benchmark_refresh_job,
)

logger = logging.getLogger(__name__)

BENCHMARK_TIMEZONE = "Asia/Kolkata"
# Three EOD-catch passes per day (IST). NSE publishes EOD in the evening; the
# earlier passes harmlessly re-confirm the prior day's close (idempotent upsert).
BENCHMARK_REFRESH_HOURS = (9, 14, 21)
BENCHMARK_REFRESH_MINUTE = 30

_benchmark_scheduler: Optional[Any] = None


def start_benchmark_scheduler() -> Optional[Any]:
    global _benchmark_scheduler
    if _benchmark_scheduler is not None:
        return _benchmark_scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        logger.warning(
            "apscheduler not installed; benchmark refresh disabled. (%s)", exc
        )
        return None

    sched = AsyncIOScheduler(
        timezone=BENCHMARK_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    sched.add_job(
        run_benchmark_refresh_job,
        trigger=CronTrigger(
            hour=",".join(str(h) for h in BENCHMARK_REFRESH_HOURS),
            minute=BENCHMARK_REFRESH_MINUTE,
            second=0,
            timezone=BENCHMARK_TIMEZONE,
        ),
        id="benchmark_thrice_daily_refresh",
        name="Thrice-daily benchmark EOD refresh (09:30/14:30/21:30 IST)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    sched.start()
    _benchmark_scheduler = sched

    for job in sched.get_jobs():
        logger.info(
            "Benchmark scheduler: [%s] %s — next run: %s",
            job.id,
            job.name,
            job.next_run_time,
        )
    return sched


async def shutdown_benchmark_scheduler() -> None:
    global _benchmark_scheduler
    if _benchmark_scheduler is None:
        return
    try:
        _benchmark_scheduler.shutdown(wait=False)
        logger.info("Benchmark scheduler shut down cleanly")
    except Exception:
        logger.exception("Benchmark scheduler shutdown failed")
    finally:
        _benchmark_scheduler = None
