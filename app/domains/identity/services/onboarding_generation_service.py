"""Onboarding personalisation job — the real work behind "Generate my portfolio".

The end-of-onboarding button used to be cosmetic (the loading page ran a fixed
3.5s timer). This service makes it real: ``run_onboarding_generation`` is the
BackgroundTasks entrypoint that

1. recalculates the effective risk profile from the just-saved answers, and
2. builds the user's daily net-worth NAV history (coordinating with — never
   duplicating — the backfill the CAS upload may already have started).

The goal/cashflow plan is deliberately NOT computed here — it runs later, when
the user completes their detailed profile (product call 2026-07-29).

Every step updates a polled ``onboarding_generation_jobs`` row (progress % +
customer-facing message) that ``GET /onboarding/generate/status`` serves to the
loading page. Step 2 is best-effort: a failure logs, the message moves on, and
the job still succeeds — onboarding must never dead-end on a compute error,
because every product surface can also compute lazily on first read.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _get_session_factory
from app.domains.identity.models.onboarding_generation_job import (
    OnboardingGenerationJob,
)

logger = logging.getLogger(__name__)

# (phase key, customer-facing label) in run order. The status endpoint derives
# the loading page's checklist from this single list — keep keys in sync with
# the ``phase`` values written below.
GENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("risk", "Analysing your risk profile"),
    ("networth", "Building your day-by-day net worth history"),
)


async def has_mf_transactions(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when the user has imported at least one mutual-fund transaction."""
    from app.domains.mutual_funds.models import MfTransaction

    return (
        await db.execute(
            select(MfTransaction.id).where(MfTransaction.user_id == user_id).limit(1)
        )
    ).first() is not None


def generation_steps(has_holdings: bool) -> tuple[tuple[str, str], ...]:
    """The checklist this user's job will actually run.

    CAMS is optional, so a user can finish onboarding with no transactions at
    all. There is then no net-worth series to build — and showing "Building your
    day-by-day net worth history" while nothing happens is a claim we can't
    back. Drop the step instead of narrating imaginary work.
    """
    if has_holdings:
        return GENERATION_STEPS
    return tuple(step for step in GENERATION_STEPS if step[0] != "networth")

# Progress bands per phase (start %, end %). The net-worth build dominates
# because it is the genuinely slow part (per-fund NAV fetches + daily replay).
_RISK_BAND = (2.0, 10.0)
_NETWORTH_BAND = (12.0, 98.0)

# The net-worth phase mirrors a ``portfolio_networth_jobs`` row (ours or the one
# the CAS upload auto-started). Cap the wait so a stuck build can never hang the
# loading page forever — the portfolio chart has its own resume/retry UI.
_NETWORTH_WAIT_TIMEOUT_S = 15 * 60
_NETWORTH_POLL_S = 1.2


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────── Job lifecycle ───────────────────────────


async def create_job(db: AsyncSession, user_id: uuid.UUID) -> OnboardingGenerationJob:
    job = OnboardingGenerationJob(
        user_id=user_id, status="pending", phase="queued", progress_pct=0
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_latest_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[OnboardingGenerationJob]:
    return (
        await db.execute(
            select(OnboardingGenerationJob)
            .where(OnboardingGenerationJob.user_id == user_id)
            .order_by(OnboardingGenerationJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_running_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[OnboardingGenerationJob]:
    return (
        await db.execute(
            select(OnboardingGenerationJob)
            .where(
                OnboardingGenerationJob.user_id == user_id,
                OnboardingGenerationJob.status.in_(("pending", "running")),
            )
            .order_by(OnboardingGenerationJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _update(db: AsyncSession, job_id: uuid.UUID, **fields: object) -> None:
    fields["updated_at"] = func.now()
    await db.execute(
        update(OnboardingGenerationJob)
        .where(OnboardingGenerationJob.id == job_id)
        .values(**fields)
    )
    await db.commit()


# ─────────────────────────── Background entrypoint ───────────────────────────


async def run_onboarding_generation(user_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Run every personalisation step, updating the polled job row as it goes.

    Opens a fresh session because the request-scoped one is closed by the time a
    BackgroundTasks callback fires.
    """
    factory = _get_session_factory()
    async with factory() as db:
        try:
            # No imported transactions (CAMS skipped) → there is no net-worth
            # series to build, so that phase is skipped entirely rather than
            # spun through for show. The status endpoint hides it too, from the
            # same predicate.
            has_holdings = await has_mf_transactions(db, user_id)

            await _update(
                db,
                job_id,
                status="running",
                phase="risk",
                progress_pct=_RISK_BAND[0],
                started_at=_now(),
                message="Analysing your risk profile…",
            )
            await _step_risk(db, user_id)
            await _update(
                db, job_id, progress_pct=_RISK_BAND[1] if has_holdings else 90.0
            )

            if has_holdings:
                await _update(
                    db,
                    job_id,
                    phase="networth",
                    progress_pct=_NETWORTH_BAND[0],
                    message="Building your day-by-day net worth history…",
                )
                await _step_networth(db, user_id, job_id)

            await _update(
                db,
                job_id,
                status="success",
                phase="done",
                progress_pct=100,
                finished_at=_now(),
                message="Your personalised portfolio is ready.",
            )
        except Exception as exc:  # noqa: BLE001 — surface failure to the poller
            logger.exception("onboarding generation job %s failed", job_id)
            try:
                await _update(
                    db,
                    job_id,
                    status="failed",
                    finished_at=_now(),
                    message=f"Failed: {exc}"[:300],
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not mark onboarding job %s failed", job_id)


# ─────────────────────────── Steps ───────────────────────────


async def _step_risk(db: AsyncSession, user_id: uuid.UUID) -> None:
    from app.domains.profile.services._effective_risk import (
        maybe_recalculate_effective_risk,
    )

    try:
        await maybe_recalculate_effective_risk(db, user_id, "onboarding_generation")
        await db.commit()
    except Exception:  # noqa: BLE001 — best effort, the chat path recalculates too
        await db.rollback()
        logger.warning(
            "onboarding generation: effective-risk recalc skipped", exc_info=True
        )


async def _step_networth(
    db: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID
) -> None:
    """Ensure the daily net-worth series exists, mirroring real backfill progress.

    Three cases: (a) the CAS upload's auto-backfill is still running — watch it;
    (b) no series yet — start a backfill ourselves and watch that; (c) series
    already built — cheap top-up through today and move on.
    """
    from app.domains.portfolio.models.portfolio_networth_job import (
        PortfolioNetworthJob,
    )
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )
    from app.domains.portfolio.services import networth_history_service as nw

    watch_id: Optional[uuid.UUID] = None
    running = await nw.has_running_job(db, user_id)
    if running is not None:
        watch_id = running.id
    else:
        has_rows = (
            (
                await db.execute(
                    select(func.count())
                    .select_from(UserPortfolioNavHistory)
                    .where(UserPortfolioNavHistory.user_id == user_id)
                )
            ).scalar_one()
            or 0
        ) > 0
        if has_rows:
            try:
                await nw.ensure_history_current_through_today(
                    db, user_id, allow_full_rebuild=False
                )
            except Exception:  # noqa: BLE001 — the chart read path self-heals too
                await db.rollback()
                logger.warning(
                    "onboarding generation: net-worth top-up failed", exc_info=True
                )
            await _update(
                db,
                job_id,
                progress_pct=_NETWORTH_BAND[1],
                message="Your net worth history is ready.",
            )
            return
        nw_job = await nw.create_job(db, user_id)
        task = asyncio.create_task(nw.run_networth_backfill(user_id, nw_job.id))
        # The backfill marks its own job row failed on error; consume the task
        # result so a crash never surfaces as "exception was never retrieved".
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        watch_id = nw_job.id

    lo, hi = _NETWORTH_BAND
    deadline = asyncio.get_event_loop().time() + _NETWORTH_WAIT_TIMEOUT_S
    while True:
        await asyncio.sleep(_NETWORTH_POLL_S)
        # A dropped DB connection mid-poll (flaky network) must not kill the
        # whole generation job — roll the session back and keep watching until
        # the deadline; the pooled connection re-establishes on the next query.
        try:
            row = (
                await db.execute(
                    select(
                        PortfolioNetworthJob.status,
                        PortfolioNetworthJob.progress_pct,
                        PortfolioNetworthJob.message,
                    ).where(PortfolioNetworthJob.id == watch_id)
                )
            ).first()
            if row is None:
                break
            nw_status, nw_pct, nw_msg = row
            frac = min(max(float(nw_pct or 0) / 100.0, 0.0), 1.0)
            await _update(
                db,
                job_id,
                progress_pct=round(lo + frac * (hi - lo), 2),
                message=nw_msg or "Building your day-by-day net worth history…",
            )
            if nw_status in ("success", "failed"):
                if nw_status == "failed":
                    # Non-fatal: the portfolio chart offers its own retry.
                    logger.warning(
                        "onboarding generation: net-worth backfill failed — continuing"
                    )
                break
        except Exception:  # noqa: BLE001 — transient DB blip; retry until deadline
            logger.warning(
                "onboarding generation: net-worth poll blip — retrying", exc_info=True
            )
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
        if asyncio.get_event_loop().time() > deadline:
            logger.warning(
                "onboarding generation: net-worth wait timed out — continuing"
            )
            break


