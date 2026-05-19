"""Daily scheduler for mfapi.in NAV refresh while backend is running.

Two scheduled jobs:

1. **Daily NAV refresh** (00:05 IST) — for every scheme in ``mf_fund_metadata``,
   calls mfapi.in only when the stored latest NAV is older than yesterday.
   Processes stale schemes in small phases (bounded memory), inserts only NAV
   points newer than the per-scheme high-water mark, then rebuilds
   ``user_mf_latest_snapshot``.
2. No periodic autofill — on-demand refresh when viewing a fund page handles
   one-off gaps; the daily job covers the full universe.

Execution is serialized across uvicorn workers via Postgres advisory locks.

Started/stopped from ``app.main`` lifespan and gated by ``MFAPI_SCHEDULER_ENABLED``.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.services.mf.latest_snapshot_service import rebuild_all_users_latest_snapshot
from app.services.mf.mfapi_ingest_service import (
    IngestMode,
    MfapiIngestError,
    ingest_mfapi,
    list_scheme_codes_needing_nav_refresh,
)

logger = logging.getLogger(__name__)


MFAPI_LOCK_KEY = 7421100
MFAPI_TIMEZONE = "Asia/Kolkata"
MFAPI_DAILY_HOUR = 0
MFAPI_DAILY_MINUTE = 5

# Schemes per ingest call — keeps peak RAM proportional to one phase, not ~8k schemes.
MFAPI_DAILY_PHASE_SIZE = 150
MFAPI_DAILY_CONCURRENCY = 8

_scheduler: Optional[Any] = None


def _min_nav_date_for_daily_refresh() -> date:
    """Schemes with latest NAV before this date need a mfapi.in pull."""
    return date.today() - timedelta(days=1)


async def _rebuild_latest_snapshots(db) -> tuple[int, int]:
    users, rows = await rebuild_all_users_latest_snapshot(db)
    return users, rows


async def run_daily_mfapi_job() -> None:
    """Incremental daily NAV for all metadata schemes, phased; then snapshot rebuild."""
    from app.database import _get_session_factory

    logger.info("mfapi daily job: starting")
    t0 = time.monotonic()
    min_nav = _min_nav_date_for_daily_refresh()
    factory = _get_session_factory()
    try:
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
                stale_codes, total_meta = await list_scheme_codes_needing_nav_refresh(
                    db, min_nav_date=min_nav,
                )
                up_to_date = total_meta - len(stale_codes)
                logger.info(
                    "mfapi daily job: %d/%d schemes already have NAV on or after %s; "
                    "%d need refresh",
                    up_to_date, total_meta, min_nav, len(stale_codes),
                )

                total_nav_inserted = 0
                total_failed = 0
                if stale_codes:
                    phases = (
                        len(stale_codes) + MFAPI_DAILY_PHASE_SIZE - 1
                    ) // MFAPI_DAILY_PHASE_SIZE
                    for phase_idx in range(phases):
                        start = phase_idx * MFAPI_DAILY_PHASE_SIZE
                        chunk = stale_codes[start : start + MFAPI_DAILY_PHASE_SIZE]
                        phase_t0 = time.monotonic()
                        logger.info(
                            "mfapi daily job: phase %d/%d — %d schemes",
                            phase_idx + 1, phases, len(chunk),
                        )
                        result = await ingest_mfapi(
                            db,
                            mode=IngestMode.INCREMENTAL,
                            scheme_codes=chunk,
                            concurrency=MFAPI_DAILY_CONCURRENCY,
                        )
                        total_nav_inserted += result.nav_rows_inserted
                        total_failed += len(result.failed_codes)
                        logger.info(
                            "mfapi daily job: phase %d/%d done in %.1fs — "
                            "nav_inserted=%d failed=%d",
                            phase_idx + 1,
                            phases,
                            time.monotonic() - phase_t0,
                            result.nav_rows_inserted,
                            len(result.failed_codes),
                        )
                else:
                    logger.info("mfapi daily job: no NAV refresh needed")

                snap_t0 = time.monotonic()
                users, snap_rows = await _rebuild_latest_snapshots(db)
                logger.info(
                    "mfapi daily job: snapshot rebuild in %.1fs — users=%d rows=%d",
                    time.monotonic() - snap_t0, users, snap_rows,
                )

                elapsed = time.monotonic() - t0
                logger.info(
                    "mfapi daily job completed in %.1fs: "
                    "metadata_total=%d refreshed=%d nav_inserted=%d failed=%d",
                    elapsed,
                    total_meta,
                    len(stale_codes),
                    total_nav_inserted,
                    total_failed,
                )
            except MfapiIngestError as exc:
                logger.error(
                    "mfapi daily job failed after %.1fs: %s",
                    time.monotonic() - t0,
                    exc,
                )
            except Exception:
                logger.exception(
                    "mfapi daily job crashed after %.1fs", time.monotonic() - t0
                )
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": MFAPI_LOCK_KEY}
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "mfapi daily job: failed to release advisory lock",
                        exc_info=True,
                    )
    except SQLAlchemyError:
        logger.warning(
            "mfapi daily job: database unavailable; will retry on next schedule",
            exc_info=True,
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

    sched = AsyncIOScheduler(
        timezone=MFAPI_TIMEZONE,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
        },
    )

    sched.add_job(
        run_daily_mfapi_job,
        trigger=CronTrigger(
            hour=MFAPI_DAILY_HOUR,
            minute=MFAPI_DAILY_MINUTE,
            second=0,
            timezone=MFAPI_TIMEZONE,
        ),
        id="mfapi_daily_refresh",
        name="Daily NAV refresh + snapshot (00:05 IST)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    sched.start()
    _scheduler = sched

    jobs = sched.get_jobs()
    logger.info("mfapi scheduler started with %d job(s):", len(jobs))
    for job in jobs:
        logger.info("  [%s] %s — next run: %s", job.id, job.name, job.next_run_time)
    return sched


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info("mfapi scheduler shut down cleanly")
    except Exception:
        logger.exception("mfapi scheduler shutdown failed")
    finally:
        _scheduler = None
