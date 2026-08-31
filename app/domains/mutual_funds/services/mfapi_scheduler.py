"""Scheduler for mfapi.in NAV refresh while backend is running.

Scheduled jobs:

1. **NAV refresh + snapshot** (00:05 / 13:05 / 22:05 IST) — for every scheme in
   ``mf_fund_metadata``, calls mfapi.in only when the stored latest NAV is older
   than the run's target date (see ``_min_nav_date_for_daily_refresh``: yesterday
   for the early runs, today for the late-evening run so same-day / late-publishing
   "bottleneck" funds get pulled). Processes stale schemes in small phases (bounded
   memory), inserts only NAV points newer than the per-scheme high-water mark, then
   rebuilds ``user_mf_latest_snapshot``. Runs three times a day, each shortly before
   a portfolio-value refresh, so the net-worth job revalues against fresh NAV.
2. No periodic autofill — on-demand refresh when viewing a fund page handles
   one-off gaps; this job covers the full universe.

Execution is serialized across uvicorn workers via Postgres advisory locks.

Started/stopped from ``app.main`` lifespan and gated by ``MFAPI_SCHEDULER_ENABLED``.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.job_tracing import (
    job_span,
    report_job_failure,
    suppress_instrumentation,
    traced_job,
)
from app.domains.mutual_funds.services.latest_snapshot_service import (
    rebuild_all_users_latest_snapshot,
)
from app.domains.mutual_funds.services.mfapi_ingest_service import (
    IngestMode,
    MfapiIngestError,
    ingest_mfapi,
    list_scheme_codes_needing_nav_refresh,
)

logger = logging.getLogger(__name__)


MFAPI_LOCK_KEY = 7421100
MFAPI_TIMEZONE = "Asia/Kolkata"
# Three runs a day, each just before a portfolio-value refresh (06:00 / 14:00 / 23:00).
MFAPI_DAILY_HOURS = "0,13,22"
MFAPI_DAILY_MINUTE = 5

# Schemes per ingest call — keeps peak RAM proportional to one phase, not ~8k schemes.
MFAPI_DAILY_PHASE_SIZE = 150
MFAPI_DAILY_CONCURRENCY = 8

# Above this share of the run failing, mfapi.in is down rather than flapping and
# a retry sweep would just double the load for nothing.
MFAPI_SWEEP_MAX_SHARE = 0.5

_scheduler: Optional[Any] = None


def _min_nav_date_for_daily_refresh() -> date:
    """Date a scheme's latest NAV must reach to be treated as current for this run.

    Schemes whose latest stored NAV is *before* this date get a mfapi.in pull. AMC
    NAVs for a trading day publish that evening (~21:00–23:00 IST), so:

    - **Late-evening run (>= 21:00 IST)** targets *today* — it pulls same-day and
      late-publishing ("bottleneck") funds so the 23:00 portfolio-value job revalues
      against today's NAV.
    - **Earlier runs** target *yesterday* — today's NAV doesn't exist yet, so aiming
      for today would pointlessly re-fetch the whole universe; yesterday keeps the
      morning/midday passes light (only true stragglers are stale).

    IST is a fixed UTC+5:30 (no DST), so we derive it without a tz database.
    """
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_ist = now_ist.date()
    if now_ist.hour >= 21:
        return today_ist
    return today_ist - timedelta(days=1)


async def _sweep_failed_codes(
    db, failed_codes: list[str], attempted: int
) -> tuple[int, int]:
    """Re-fetch the schemes that lost the coin flip against a flapping upstream.

    mfapi.in 502s a large share of requests, in bursts that outlast the
    per-request retry ladder in ``mfapi_fetcher``. The failures are NOT sticky
    per scheme, so one sweep minutes later recovers most of them — without it a
    scheme stays stale until the next run, 8–11 hours away.

    Returns (NAV rows inserted by the sweep, schemes still unfetched).
    """
    if attempted and len(failed_codes) > attempted * MFAPI_SWEEP_MAX_SHARE:
        logger.warning(
            "mfapi daily job: %d/%d schemes failed — upstream looks down, "
            "skipping the retry sweep",
            len(failed_codes),
            attempted,
        )
        return 0, len(failed_codes)

    logger.info(
        "mfapi daily job: retry sweep over %d failed schemes", len(failed_codes)
    )
    result = await ingest_mfapi(
        db,
        mode=IngestMode.INCREMENTAL,
        scheme_codes=failed_codes,
        concurrency=MFAPI_DAILY_CONCURRENCY,
    )
    if result.failed_codes:
        # The only per-scheme record of a failure now that the fetcher logs at
        # DEBUG — one capped line a run instead of ~1.5K.
        logger.warning(
            "mfapi daily job: %d schemes still stale after the retry sweep: %s%s",
            len(result.failed_codes),
            ", ".join(result.failed_codes[:20]),
            " …" if len(result.failed_codes) > 20 else "",
        )
    return result.nav_rows_inserted, len(result.failed_codes)


async def _rebuild_latest_snapshots(db) -> tuple[int, int]:
    users, rows = await rebuild_all_users_latest_snapshot(db)
    return users, rows


@traced_job("mfapi.daily_job")
async def run_daily_mfapi_job() -> None:
    """Incremental daily NAV for all metadata schemes, phased; then snapshot rebuild."""
    from app.core.database import _get_session_factory

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
                    db,
                    min_nav_date=min_nav,
                )
                up_to_date = total_meta - len(stale_codes)
                logger.info(
                    "mfapi daily job: %d/%d schemes already have NAV on or after %s; "
                    "%d need refresh",
                    up_to_date,
                    total_meta,
                    min_nav,
                    len(stale_codes),
                )

                total_nav_inserted = 0
                total_failed = 0
                failed_codes: list[str] = []
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
                            phase_idx + 1,
                            phases,
                            len(chunk),
                        )
                        # One span per PHASE, never per scheme: SQLAlchemy and
                        # httpx are instrumented globally (app/main.py), so
                        # without the suppression a ~8k-scheme sweep would emit
                        # ~25k spans into one trace nothing can render.
                        with (
                            job_span(
                                "mfapi.daily_job.phase",
                                phase=phase_idx + 1,
                                phases=phases,
                                schemes=len(chunk),
                            ),
                            suppress_instrumentation(),
                        ):
                            result = await ingest_mfapi(
                                db,
                                mode=IngestMode.INCREMENTAL,
                                scheme_codes=chunk,
                                concurrency=MFAPI_DAILY_CONCURRENCY,
                            )
                        total_nav_inserted += result.nav_rows_inserted
                        total_failed += len(result.failed_codes)
                        failed_codes.extend(result.failed_codes)
                        logger.info(
                            "mfapi daily job: phase %d/%d done in %.1fs — "
                            "nav_inserted=%d failed=%d",
                            phase_idx + 1,
                            phases,
                            time.monotonic() - phase_t0,
                            result.nav_rows_inserted,
                            len(result.failed_codes),
                        )
                    if failed_codes:
                        # Retried once here rather than left for the next run:
                        # the upstream's 502s are transient and unsticky, so a
                        # second pass converts most of them. Span-suppressed for
                        # the same reason the phases are — one span, not one
                        # per scheme.
                        with (
                            job_span(
                                "mfapi.daily_job.sweep",
                                schemes=len(failed_codes),
                            ),
                            suppress_instrumentation(),
                        ):
                            swept_nav, total_failed = await _sweep_failed_codes(
                                db, failed_codes, len(stale_codes)
                            )
                        total_nav_inserted += swept_nav
                else:
                    logger.info("mfapi daily job: no NAV refresh needed")

                snap_t0 = time.monotonic()
                users, snap_rows = await _rebuild_latest_snapshots(db)
                logger.info(
                    "mfapi daily job: snapshot rebuild in %.1fs — users=%d rows=%d",
                    time.monotonic() - snap_t0,
                    users,
                    snap_rows,
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
            # These handlers SWALLOW the exception, so the run span created by
            # @traced_job never sees it — report explicitly or the job stays
            # green in the trace and absent from Error Tracking. Reporting is
            # deduped, so a failure already filed by the phase span files once.
            except MfapiIngestError as exc:
                logger.error(
                    "mfapi daily job failed after %.1fs: %s",
                    time.monotonic() - t0,
                    exc,
                )
                report_job_failure(exc, job="mfapi.daily_job")
            except Exception as exc:
                logger.exception(
                    "mfapi daily job crashed after %.1fs", time.monotonic() - t0
                )
                report_job_failure(exc, job="mfapi.daily_job")
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
            hour=MFAPI_DAILY_HOURS,
            minute=MFAPI_DAILY_MINUTE,
            second=0,
            timezone=MFAPI_TIMEZONE,
        ),
        id="mfapi_daily_refresh",
        name="NAV refresh + snapshot (00:05 / 13:05 / 22:05 IST)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Refresh held-fund NAV and bring every user's net-worth series through today.
    # Runs three times a day so late-publishing ("bottleneck") funds get picked up and
    # the dashboard value/chart stay current (each run is after the morning NAV refresh
    # or catches intraday/evening NAV publishes). Imported lazily to avoid a
    # cross-domain import cycle at module load.
    from app.domains.portfolio.services.networth_history_service import (
        run_daily_networth_job,
    )

    sched.add_job(
        run_daily_networth_job,
        trigger=CronTrigger(
            hour="6,14,23",
            minute=0,
            second=0,
            timezone=MFAPI_TIMEZONE,
        ),
        id="portfolio_networth_daily",
        name="Portfolio value refresh (06:00 / 14:00 / 23:00 IST)",
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
