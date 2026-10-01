"""Silent rebalancing refresh after new transactions land.

A CAS ingest wipes the user's computed plans (``cache_invalidation_service``), so
until now the first visit to the Invest page paid for a full compute while the
customer watched a progress bar. This runs that compute for them in the
background instead: the net-worth builder calls ``schedule_rebalancing_refresh``
when a build that carried new transactions finishes, and the plan is already on
record by the time anyone opens the page.

Four rules, each one a way this could go quietly wrong:

**A statement that already has a plan is left alone.** The customer can reach the
Invest page before the net-worth build finishes, and that page computes a plan
itself when it finds none. So the refresh first waits out any compute the page has
in flight, then looks for a plan stamped with the ACTIVE statement and skips when
one exists — computing again would only stack a second, identical run on top of
the one the customer is already reading.

**Nothing is published to the frontend.** ``compute_rebalancing_result`` is called
without a ``progress`` writer, so the Invest page's compute poller never sees this
run — the page simply finds a plan when it reads ``/rebalancing/current``.

**The snapshot is resolved here, never inherited.** The caller is a net-worth
build that pinned its snapshot when it *started*; a statement uploaded while it
ran is the one that is active now. The task therefore starts in an empty context
and looks the active snapshot up itself, after the delay.

**It never raises and never blocks the caller.** A blocked or failed compute
leaves the user exactly where they were before this existed: the Invest page
computes a plan on first visit.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from typing import Optional

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import (
    resolve_active_cas_upload_id,
    scoped_to,
    versioning_enabled,
)
from app.core.database import _get_session_factory
from app.core.job_tracing import record_job_counts, traced_job
from app.core.progress import REBALANCE_COMPUTE_TASK, get_progress
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
from app.domains.rebalancing.services.saved_plan_service import committed_run_filter

logger = logging.getLogger(__name__)

# How long after the net-worth history is written the rebalance starts. Short on
# purpose: it only has to let the build's final writes and status update settle.
AUTO_REFRESH_DELAY_S = 1.0

# How often, and for how long at most, to wait on a compute the Invest page already
# has in flight before deciding for ourselves. The ceiling sits just past the
# progress store's own expiry, so a compute that died without clearing its entry
# cannot hold the refresh hostage.
_PAGE_COMPUTE_POLL_S = 1.0
_PAGE_COMPUTE_MAX_WAIT_S = 310.0

_JOB_NAME = "rebalancing.auto_refresh"

# ``asyncio`` keeps only a weak reference to a task. Without a strong one here a
# fire-and-forget task can be garbage-collected mid-compute.
_TASKS: set[asyncio.Task] = set()

# Users with a refresh queued or running in THIS process. A second request while
# one is in flight asks for one more pass instead of a second concurrent compute.
_IN_FLIGHT: set[uuid.UUID] = set()
_RERUN: set[uuid.UUID] = set()


def schedule_rebalancing_refresh(
    user_id: uuid.UUID,
    *,
    reason: str,
    delay_s: float = AUTO_REFRESH_DELAY_S,
) -> Optional[asyncio.Task]:
    """Fire-and-forget a background rebalancing compute for ``user_id``.

    Returns the task, or None when one is already in flight for this user (that
    one is flagged to run again, so the newest data still gets a plan).
    """
    if user_id in _IN_FLIGHT:
        _RERUN.add(user_id)
        logger.info(
            "rebalancing auto-refresh already in flight for user %s; will re-run",
            user_id,
        )
        return None

    _IN_FLIGHT.add(user_id)
    # An EMPTY context, not a copy of the caller's: the caller is inside a pinned
    # CAS scope and its own job span, and neither belongs to this job.
    task = asyncio.get_running_loop().create_task(
        _refresh_until_settled(user_id, reason=reason, delay_s=delay_s),
        context=contextvars.Context(),
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _refresh_until_settled(
    user_id: uuid.UUID, *, reason: str, delay_s: float
) -> None:
    """Run the refresh, and once more if fresher data was announced meanwhile."""
    try:
        while True:
            _RERUN.discard(user_id)
            try:
                await refresh_rebalancing_plan(user_id, reason=reason, delay_s=delay_s)
            except Exception:  # noqa: BLE001 - already logged and reported
                pass
            if user_id not in _RERUN:
                break
    finally:
        _IN_FLIGHT.discard(user_id)
        _RERUN.discard(user_id)


async def _wait_for_page_compute(user_id: uuid.UUID) -> bool:
    """Wait while the Invest page is computing this user's plan. True if we waited.

    Waiting rather than skipping outright: that compute may still fail, and then
    this refresh is the only thing left that will produce a plan. Whether it
    succeeded is answered afterwards, by looking for the run it would have saved.

    The store is per process, so with several workers a compute on another one is
    invisible here — the plan lookup that follows is the check that always holds.
    """
    waited = False
    deadline = time.monotonic() + _PAGE_COMPUTE_MAX_WAIT_S
    while get_progress(user_id, REBALANCE_COMPUTE_TASK)["active"]:
        if time.monotonic() >= deadline:
            break
        waited = True
        await asyncio.sleep(_PAGE_COMPUTE_POLL_S)
    return waited


def _existing_plan_stmt(
    user_id: uuid.UUID, snapshot_id: Optional[uuid.UUID]
) -> Select[tuple[uuid.UUID]]:
    """The newest plan already computed for the statement that is active now.

    "For this statement" is the run's own ``cas_upload_id``, compared explicitly.
    The read hook alone is not enough: it also lets through rows that belong to no
    statement, and a plan nobody stamped proves nothing about the new upload.

    With no snapshot (versioning off, or nothing active) any committed run counts —
    there the ingest deletes every earlier run, so whatever is here came after it.

    ``committed_run_filter`` keeps an unsaved chat what-if from passing as the
    customer's plan; it is the same filter the Invest page reads through.
    """
    stmt = select(RebalancingRun.id).where(
        RebalancingRun.user_id == user_id, committed_run_filter()
    )
    if snapshot_id is not None:
        stmt = stmt.where(RebalancingRun.cas_upload_id == snapshot_id)
    return stmt.order_by(RebalancingRun.created_at.desc()).limit(1)


async def _existing_plan_id(
    db: AsyncSession, user_id: uuid.UUID, snapshot_id: Optional[uuid.UUID]
) -> Optional[uuid.UUID]:
    return (
        await db.execute(_existing_plan_stmt(user_id, snapshot_id))
    ).scalar_one_or_none()


@traced_job(_JOB_NAME)
async def refresh_rebalancing_plan(
    user_id: uuid.UUID,
    *,
    reason: str,
    delay_s: float = AUTO_REFRESH_DELAY_S,
) -> Optional[uuid.UUID]:
    """Compute and persist a fresh rebalancing plan. Returns the run id, if any.

    Returns None without computing when the active statement already has a plan
    (see the module docstring) — the id returned is only ever one this call made.

    The run is an ordinary computed one (``origin=None``): it becomes the plan the
    Invest page shows, and it can never outrank a plan the customer saved.
    """
    # Lazy: these pull in the whole engine stack, and this module is imported by
    # the net-worth builder's hook.
    from app.domains.identity.services.user_context_loader import load_user_for_ai
    from app.domains.rebalancing.services.rebal_engine.service import (
        compute_rebalancing_result,
    )

    if delay_s > 0:
        await asyncio.sleep(delay_s)
    waited_for_page = await _wait_for_page_compute(user_id)

    factory = _get_session_factory()
    async with factory() as db:
        try:
            # Resolved AFTER the waits above, so it is the statement active now.
            snapshot_id = (
                await resolve_active_cas_upload_id(db, user_id)
                if versioning_enabled()
                else None
            )
            with scoped_to(snapshot_id):
                existing = await _existing_plan_id(db, user_id, snapshot_id)
                if existing is not None:
                    logger.info(
                        "rebalancing auto-refresh skipped for user %s (%s): "
                        "run %s already covers the active statement",
                        user_id,
                        reason,
                        existing,
                    )
                    record_job_counts(
                        plan="already_computed", waited_for_page=waited_for_page
                    )
                    return None

                user = await load_user_for_ai(db, user_id)
                if user is None:
                    logger.info(
                        "rebalancing auto-refresh: user %s no longer exists", user_id
                    )
                    record_job_counts(plan="user_missing")
                    return None

                outcome = await compute_rebalancing_result(
                    user,
                    f"auto refresh ({reason})",
                    db=db,
                    acting_user_id=user_id,
                    chat_session_id=None,
                    persist=True,
                    origin=None,
                )
                await db.commit()
        except Exception:
            # Re-raised so the job span marks the run failed and files the issue;
            # ``_refresh_until_settled`` is what keeps it away from the caller.
            logger.exception(
                "rebalancing auto-refresh failed for user %s (%s)", user_id, reason
            )
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                logger.debug(
                    "rollback after auto-refresh failure failed", exc_info=True
                )
            raise

    if outcome.blocking_message is not None:
        # Not an error: a missing date of birth or an empty portfolio. The Invest
        # page's own gate asks the customer for whatever is missing.
        logger.info(
            "rebalancing auto-refresh skipped for user %s (%s): %s",
            user_id,
            reason,
            outcome.blocking_message,
        )
        record_job_counts(plan="blocked")
        return None

    logger.info(
        "rebalancing auto-refresh done for user %s (%s): run %s",
        user_id,
        reason,
        outcome.recommendation_id,
    )
    record_job_counts(
        plan="computed",
        used_cached_allocation=bool(outcome.used_cached_allocation),
    )
    return outcome.recommendation_id
