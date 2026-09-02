"""Scheduled VR mirror refresh. Off by default.

Mirrors the shape of ``mfapi_scheduler`` — APScheduler, IST, serialized across
uvicorn workers — with one difference: the per-table advisory lock lives in
:func:`~.services.sync_service.sync_table`, not here, so a manual ops trigger is
serialized against the scheduler too.

Hours are per table (see each spec's ``schedule_hours``) rather than one nightly
sweep, because the tables have genuinely different cadences: NAV is daily,
holdings are monthly, and masters barely change. A single job would drag a
monthly 5-million-row holdings walk through every nightly run.

Gated by ``VR_SYNC_ENABLED``, which defaults to **false** — the opposite of
``MFAPI_SCHEDULER_ENABLED``. A vendor mirror should not start pulling because
someone deployed; it starts when someone decides it should.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import _get_session_factory
from app.core.job_tracing import report_job_failure, traced_job
from app.domains.vr_data.services.crosswalk_service import rebuild_crosswalk
from app.domains.vr_data.services.sync_service import enabled_specs, run_cycle

logger = logging.getLogger(__name__)

VR_TIMEZONE = "Asia/Kolkata"
_scheduler: Optional[Any] = None


def scheduled_hours() -> list[int]:
    """Distinct IST hours any enabled table wants."""
    hours: set[int] = set()
    for spec in enabled_specs():
        hours.update(spec.schedule_hours)
    return sorted(hours)


@traced_job("vr.sync_cycle")
async def run_scheduled_cycle(hour: int) -> None:
    """Sync every enabled table scheduled for ``hour``, then re-link."""
    started = time.monotonic()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            results = await run_cycle(db, hour=hour)
            ok = [r for r in results if r.ok and not r.skipped_reason]
            failed = [r for r in results if not r.ok]
            skipped = [r for r in results if r.skipped_reason]
            logger.info(
                "vr sync %02d:00 IST — %d ok (%d rows), %d failed, %d skipped, %.1fs",
                hour,
                len(ok),
                sum(r.rows_written for r in ok),
                len(failed),
                len(skipped),
                time.monotonic() - started,
            )
            for result in failed:
                logger.error("vr sync %s failed: %s", result.table, result.error)
                if result.access_denied_by_vendor:
                    logger.error(
                        "vr sync %s: VR refused this table for our key — this is a "
                        "contract question, not a retryable error. Disable it in "
                        "VR_SYNC_TIERS or raise it with the vendor.",
                        result.table,
                    )

            # The crosswalk is only as good as the masters behind it, so rebuild
            # it whenever one of its two inputs was refreshed.
            if any(
                r.table in {"subplan_isin", "fund_basic_details"} and r.ok
                for r in results
            ):
                report = await rebuild_crosswalk(db)
                logger.info(
                    "vr crosswalk: %d links, %.2f%% of active schemes",
                    report.total_links,
                    report.coverage_pct,
                )
    except SQLAlchemyError as exc:
        logger.warning("vr sync: database unavailable; retrying next schedule")
        report_job_failure(exc, job="vr.sync_cycle")
    except Exception as exc:  # noqa: BLE001 - a scheduled job must not die silently
        logger.exception("vr sync cycle crashed")
        report_job_failure(exc, job="vr.sync_cycle")


def start_vr_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    if not settings.vr_sync_enabled():
        logger.info("vr scheduler disabled (VR_SYNC_ENABLED is not true)")
        return
    if not settings.get_vr_api_key():
        logger.warning(
            "vr scheduler: VR_SYNC_ENABLED is true but VR_API_KEY is unset — "
            "not starting."
        )
        return
    if _scheduler is not None:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    _scheduler = AsyncIOScheduler(timezone=VR_TIMEZONE)
    hours = scheduled_hours()
    for hour in hours:
        _scheduler.add_job(
            run_scheduled_cycle,
            CronTrigger(hour=hour, minute=15, timezone=VR_TIMEZONE),
            args=[hour],
            id=f"vr_sync_{hour:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    _scheduler.start()
    logger.info(
        "vr scheduler started — %d table(s) across IST hours %s",
        len(enabled_specs()),
        hours,
    )


async def shutdown_vr_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover - shutdown must not raise
            logger.warning("vr scheduler shutdown failed", exc_info=True)
        _scheduler = None
