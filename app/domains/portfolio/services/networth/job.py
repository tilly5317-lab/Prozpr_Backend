"""Lifecycle of a net-worth rebuild job: single-flight, heartbeat, reaping, supersession.

Every rule in here is the residue of a specific production failure.

* **Single-flight is enforced by the database.** Check-then-create is a race, and it
  lost — one account accumulated three concurrent builds that then fought over the same
  rows. The partial unique index ``uq_networth_job_active`` makes a second live job
  impossible; ``create_job`` catches the violation and hands back the incumbent, so the
  caller gets the same answer either way without holding a lock.
* **Status writes get their own session.** This is the difference between a job that
  fails and a job that hangs. Sharing the compute session meant that when the compute
  raised, that session was already in an aborted transaction, so the handler's attempt
  to mark the job ``failed`` raised ``InFailedSQLTransactionError`` itself, was swallowed
  by its own ``except``, and the row stayed ``running`` at 98% forever. Eight jobs were
  wedged that way, the oldest for five days, with the UI politely polling throughout. A
  status write must be able to succeed *precisely when* the data write could not.
* **Stale jobs are reaped on elapsed time.** No in-process handler can cover a process
  that stops existing — a deploy restart or an OOM cancels an in-process background task
  without ever reaching an ``except``. Until an outside observer closes the job out, the
  dashboard polls something that will never move again and the single-flight guard
  blocks the very rebuild that would fix the account.
* **A newer statement supersedes an in-flight build.** ``create_job`` returning the
  incumbent is right for "user tapped Rebuild twice" and *wrong* for "a second CAS
  landed": the running job is reading the statement that was just replaced, and joining
  it means the new one is never built. Setting ``supersede_requested`` makes the worker
  run one more pass instead.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _get_session_factory
from app.domains.portfolio.models.portfolio_networth_job import PortfolioNetworthJob
from app.domains.portfolio.services.networth.clock import utc_now

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "running")

# A running job that has not written its heartbeat in this long is not running. Generous
# enough that a slow NAV-fetch phase on a large portfolio is never mistaken for a dead
# one.
JOB_STALE_AFTER = timedelta(minutes=20)

# How many times a worker will loop for a supersede request before giving up. Bounded so
# a pathological "upload every 10 seconds" cannot spin a worker forever.
MAX_ATTEMPTS = 3


async def create_job(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    trigger: str = "manual",
    supersede: bool = True,
) -> tuple[PortfolioNetworthJob, bool]:
    """Start a build for this user, or join the one already in flight.

    ``supersede`` should be True whenever the caller knows fresh data has landed (a CAS
    upload); it marks the incumbent so it re-runs rather than finishing against the
    statement that was just replaced.

    Returns ``(job, created)``. ``created`` is the caller's cue to queue the worker:
    inferring it from the row's own fields cannot distinguish "our brand-new pending
    job" from "someone else's pending job we joined", and getting that wrong queues a
    second worker against the same job — the very duplication this exists to prevent.
    """
    await reap_stale_jobs(db, user_id)

    job = PortfolioNetworthJob(
        user_id=user_id,
        status="pending",
        phase="queued",
        progress_pct=0,
        trigger=trigger,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # Another request won the race. Its job is the live one; ours never existed.
        await db.rollback()
        existing = await has_running_job(db, user_id)
        if existing is not None:
            if supersede:
                await request_supersede(db, existing.id)
                logger.info(
                    "networth build in flight for user %s; flagged job %s to re-run",
                    user_id,
                    existing.id,
                )
            else:
                logger.info(
                    "networth build already in flight for user %s; reusing job %s",
                    user_id,
                    existing.id,
                )
            return existing, False
        raise
    await db.refresh(job)
    return job, True


async def get_latest_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[PortfolioNetworthJob]:
    return (
        await db.execute(
            select(PortfolioNetworthJob)
            .where(PortfolioNetworthJob.user_id == user_id)
            .order_by(PortfolioNetworthJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_running_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[PortfolioNetworthJob]:
    return (
        await db.execute(
            select(PortfolioNetworthJob)
            .where(
                PortfolioNetworthJob.user_id == user_id,
                PortfolioNetworthJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(PortfolioNetworthJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def request_supersede(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Tell a running job that fresher data landed while it was working."""
    await db.execute(
        update(PortfolioNetworthJob)
        .where(PortfolioNetworthJob.id == job_id)
        .values(supersede_requested=True, updated_at=func.now())
    )
    await db.commit()


async def update_job(job_id: uuid.UUID, **fields: Any) -> None:
    """Write job progress on a SESSION OF ITS OWN, never the caller's.

    See the module docstring: this is what lets a failed compute still be *recorded* as
    failed. ``updated_at`` doubles as the heartbeat ``reap_stale_jobs`` reads.
    """
    fields["updated_at"] = func.now()
    factory = _get_session_factory()
    async with factory() as status_db:
        await status_db.execute(
            update(PortfolioNetworthJob)
            .where(PortfolioNetworthJob.id == job_id)
            .values(**fields)
        )
        await status_db.commit()


async def read_job(job_id: uuid.UUID) -> Optional[PortfolioNetworthJob]:
    """Re-read a job on a fresh session (used to check ``supersede_requested``)."""
    factory = _get_session_factory()
    async with factory() as db:
        return (
            await db.execute(
                select(PortfolioNetworthJob).where(PortfolioNetworthJob.id == job_id)
            )
        ).scalar_one_or_none()


async def consume_supersede(job_id: uuid.UUID) -> bool:
    """Atomically clear ``supersede_requested`` and report whether it was set.

    Atomic on purpose: a read-then-clear would drop an upload that landed between the
    two statements, which is exactly the window this flag exists to cover.
    """
    factory = _get_session_factory()
    async with factory() as db:
        result = await db.execute(
            update(PortfolioNetworthJob)
            .where(
                PortfolioNetworthJob.id == job_id,
                PortfolioNetworthJob.supersede_requested.is_(True),
                PortfolioNetworthJob.attempt < MAX_ATTEMPTS,
            )
            .values(
                supersede_requested=False,
                attempt=PortfolioNetworthJob.attempt + 1,
                updated_at=func.now(),
            )
        )
        await db.commit()
        return bool(result.rowcount)


async def reap_stale_jobs(db: AsyncSession, user_id: Optional[uuid.UUID] = None) -> int:
    """Fail every pending/running job whose heartbeat has gone quiet. Returns the count.

    Scoped to one user on the read path (cheap, self-healing when they open the page)
    and run globally by the daily job.
    """
    cutoff = utc_now() - JOB_STALE_AFTER
    stmt = (
        update(PortfolioNetworthJob)
        .where(
            PortfolioNetworthJob.status.in_(ACTIVE_STATUSES),
            PortfolioNetworthJob.updated_at < cutoff,
        )
        .values(
            status="failed",
            finished_at=utc_now(),
            updated_at=func.now(),
            message="Build was interrupted. Tap to try again.",
        )
    )
    if user_id is not None:
        stmt = stmt.where(PortfolioNetworthJob.user_id == user_id)
    result = await db.execute(stmt)
    reaped = int(result.rowcount or 0)
    if reaped:
        await db.commit()
        logger.warning("reaped %d stale net-worth job(s)", reaped)
    return reaped


async def has_reapable_job(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Cheap read: is there actually a stale job for this user worth reaping?

    The status endpoint is polled every ~1.8 seconds per open dashboard. Running the
    reaping UPDATE unconditionally on that path turns a read into a fleet-wide write
    storm; this read lets the caller skip it in the overwhelmingly common case.
    """
    cutoff = utc_now() - JOB_STALE_AFTER
    return (
        await db.execute(
            select(PortfolioNetworthJob.id)
            .where(
                PortfolioNetworthJob.user_id == user_id,
                PortfolioNetworthJob.status.in_(ACTIVE_STATUSES),
                PortfolioNetworthJob.updated_at < cutoff,
            )
            .limit(1)
        )
    ).first() is not None
