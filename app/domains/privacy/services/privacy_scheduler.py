"""Daily job: purge erased accounts, then apply retention.

Gated by ``PRIVACY_SCHEDULER_ENABLED`` and defaulting to a **dry run**
(``PRIVACY_RETENTION_APPLY``), because everything here is irreversible and the
sensible first deployment is one that reports what it would have removed.

Follows the existing scheduler pattern (``MFAPI_SCHEDULER_ENABLED``,
``BENCHMARK_SCHEDULER_ENABLED``) rather than inventing a new one.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.job_tracing import report_job_failure, traced_job
from app.core.retention import apply_retention
from app.domains.privacy.services import erasure_service

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


@traced_job("privacy_retention")
async def run_privacy_maintenance() -> dict:
    """One pass: purge accounts past their grace window, then age out old data."""
    settings = get_settings()
    apply = settings.privacy_retention_apply()
    # Same accessor the mfapi and benchmark schedulers use — imported lazily so
    # this module stays importable before the engine is built.
    from app.core.database import _get_session_factory

    session_factory = _get_session_factory()

    purged: list[str] = []
    async with session_factory() as db:
        try:
            due = await erasure_service.due_for_purge(db)
            for user_id in due:
                await erasure_service.purge_user(db, user_id, dry_run=not apply)
                purged.append(str(user_id))
            if apply and due:
                await db.commit()
            else:
                await db.rollback()
        except Exception as exc:
            await db.rollback()
            # Schedulers catch their own exceptions, so the traced_job span never
            # sees this — without an explicit report the job stays green.
            report_job_failure(exc, job="privacy_retention")
            logger.exception("Erasure purge failed.")

    retention: dict[str, int] = {}
    async with session_factory() as db:
        try:
            retention = await apply_retention(db, dry_run=not apply)
        except Exception as exc:
            await db.rollback()
            report_job_failure(exc, job="privacy_retention")
            logger.exception("Retention pass failed.")

    result = {"applied": apply, "purged_accounts": len(purged), "retention": retention}
    logger.info("Privacy maintenance complete: %s", result)
    return result


def start_privacy_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    # 03:15 IST — after the daily NAV work, well outside user hours.
    _scheduler.add_job(
        run_privacy_maintenance,
        CronTrigger(hour=3, minute=15),
        id="privacy_maintenance",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Privacy scheduler started (daily 03:15 IST).")


async def shutdown_privacy_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
