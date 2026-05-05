"""Daily scheduler for mfapi.in NAV refresh while backend is running.

Cron-fires at 00:05 IST every day. The job runs incremental ingest, which
fetches scheme details and inserts only newer NAV points per scheme, so all
schemes get today's available NAV update without duplicating old rows.

Execution is serialized across concurrent uvicorn workers via a Postgres
advisory lock so only one process performs the run.

Started/stopped from ``app.main`` lifespan and gated by
``MFAPI_SCHEDULER_ENABLED`` so test runs do not spin it up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.services.mf.mfapi_ingest_service import IngestMode, MfapiIngestError, ingest_mfapi

logger = logging.getLogger(__name__)


MFAPI_LOCK_KEY = 7421100  # constant chosen once; pg_advisory_lock namespace
MFAPI_TIMEZONE = "Asia/Kolkata"
MFAPI_DAILY_HOUR = 0
MFAPI_DAILY_MINUTE = 5
MFAPI_MISSING_NAV_CHECK_MINUTES = 30
MFAPI_MISSING_NAV_LOOKBACK_DAYS = 30

_scheduler: Optional[Any] = None


async def run_daily_mfapi_job() -> None:
    """Run one daily incremental NAV refresh; skip if another worker has lock."""
    from app.database import _get_session_factory

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
                # Incremental mode updates all schemes with only new NAV points.
                await ingest_mfapi(db, mode=IngestMode.INCREMENTAL)
            except MfapiIngestError as exc:
                logger.error("mfapi daily job failed: %s", exc)
            except Exception:
                logger.exception("mfapi daily job crashed")
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": MFAPI_LOCK_KEY}
                    )
                except SQLAlchemyError:
                    logger.warning("mfapi daily job: failed to release advisory lock", exc_info=True)
    except SQLAlchemyError:
        logger.warning("mfapi daily job: database unavailable; will retry on next schedule", exc_info=True)


async def run_missing_nav_autofill_job() -> None:
    """Auto-heal NAV gaps for schemes users actually hold/track."""
    from app.database import _get_session_factory

    cutoff = (datetime.now(timezone.utc) - timedelta(days=MFAPI_MISSING_NAV_LOOKBACK_DAYS)).date()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": MFAPI_LOCK_KEY + 1}
                )
            ).scalar()
            if not got_lock:
                logger.info("mfapi missing-nav job: lock held by another worker; skipping")
                return
            try:
                rows = await db.execute(
                    text(
                        "SELECT DISTINCT x.scheme_code "
                        "FROM ("
                        "  SELECT scheme_code FROM mf_transactions "
                        "  UNION "
                        "  SELECT scheme_code FROM mf_sip_mandates"
                        ") x "
                        "LEFT JOIN ("
                        "  SELECT scheme_code, MAX(nav_date) AS max_nav_date "
                        "  FROM mf_nav_history GROUP BY scheme_code"
                        ") h ON h.scheme_code = x.scheme_code "
                        "WHERE h.max_nav_date IS NULL OR h.max_nav_date < :cutoff "
                        "ORDER BY x.scheme_code"
                    ),
                    {"cutoff": cutoff},
                )
                stale_codes = [str(r[0]).strip() for r in rows.all() if str(r[0]).strip()]
                if not stale_codes:
                    return
                logger.info("mfapi missing-nav job: refreshing %d schemes", len(stale_codes))
                await ingest_mfapi(
                    db,
                    mode=IngestMode.INCREMENTAL,
                    scheme_codes=stale_codes,
                    concurrency=8,
                )
            except MfapiIngestError as exc:
                logger.error("mfapi missing-nav job failed: %s", exc)
            except Exception:
                logger.exception("mfapi missing-nav job crashed")
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": MFAPI_LOCK_KEY + 1}
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "mfapi missing-nav job: failed to release advisory lock",
                        exc_info=True,
                    )
    except SQLAlchemyError:
        logger.warning(
            "mfapi missing-nav job: database unavailable; will retry on next schedule",
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
    sched = AsyncIOScheduler(timezone=MFAPI_TIMEZONE)
    sched.add_job(
        run_daily_mfapi_job,
        trigger=CronTrigger(
            hour=MFAPI_DAILY_HOUR,
            minute=MFAPI_DAILY_MINUTE,
            second=0,
            timezone=MFAPI_TIMEZONE,
        ),
        id="mfapi_daily_refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    sched.add_job(
        run_missing_nav_autofill_job,
        trigger="interval",
        minutes=MFAPI_MISSING_NAV_CHECK_MINUTES,
        id="mfapi_missing_nav_autofill",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20),
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=900,
    )
    sched.start()
    _scheduler = sched
    next_daily = sched.get_job("mfapi_daily_refresh").next_run_time
    next_heal = sched.get_job("mfapi_missing_nav_autofill").next_run_time
    logger.info("mfapi scheduler started; next daily=%s, next missing-nav=%s", next_daily, next_heal)
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
