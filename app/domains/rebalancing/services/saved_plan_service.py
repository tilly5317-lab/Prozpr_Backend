"""Save + read the customer's committed rebalancing plan (spec 2026-08-27; tilt-save 2026-08-30).

``rebalancing_runs.origin`` marks provenance:
- ``'saved'``     — the customer's committed plan (exactly one per user).
- ``'candidate'`` — a tilt the customer *viewed* but has NOT committed. Persisted
                    so it can be saved on demand, but firewalled out of every
                    "committed/current plan" read until the customer saves it.
- ``NULL``        — an ordinary computed run.

Saving flips a run (plain or candidate) to ``'saved'``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rebalancing.models.rebalancing_run import RebalancingRun

ORIGIN_SAVED = "saved"
# A tilt the customer viewed but didn't commit. Persisted (so Save can flip it),
# but excluded from every "latest committed plan" read via committed_run_filter().
ORIGIN_CANDIDATE = "candidate"


def committed_run_filter():
    """Filter for the committed/current plan set: NULL (plain run) and 'saved'
    both count; an un-saved 'candidate' (viewed-but-not-committed tilt) does not.

    Every "latest committed run" read uses this — the portfolio page, the SIP
    mirror, execution, and the run list — so a tilt the customer merely looked
    at never becomes their plan or shifts their SIP/orders.
    """
    return or_(
        RebalancingRun.origin.is_(None),
        RebalancingRun.origin != ORIGIN_CANDIDATE,
    )


async def save_plan(
    db: AsyncSession, *, user_id: uuid.UUID, run_id: uuid.UUID
) -> RebalancingRun | None:
    """Mark one run as the user's committed plan (``origin='saved'``).

    Works for a plain run OR a viewed tilt ('candidate') — both flip to 'saved',
    which is what makes tilt-save work. Demotes any other saved run for the user
    so exactly one stays committed. Returns the run, or ``None`` if it does not
    exist / is not this user's. Does NOT commit — the caller owns the transaction.
    """
    run = (
        await db.execute(
            select(RebalancingRun).where(
                RebalancingRun.id == run_id,
                RebalancingRun.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    await db.execute(
        update(RebalancingRun)
        .where(
            RebalancingRun.user_id == user_id,
            RebalancingRun.origin == ORIGIN_SAVED,
            RebalancingRun.id != run_id,
        )
        .values(origin=None)
    )
    run.origin = ORIGIN_SAVED
    await db.flush()
    return run


async def select_current_run_id(
    db: AsyncSession, *, user_id: uuid.UUID
) -> uuid.UUID | None:
    """The run the portfolio page shows: the committed (saved) run if any, else
    the most-recently-created COMMITTED run. Un-saved candidates (viewed tilts)
    are excluded — the page never shows a plan the customer didn't pick.
    ``None`` if the user has no committed run.

    ``case`` (not ``origin == 'saved'`` directly) keeps NULL origins sorting
    after saved regardless of the DB's NULL-ordering rules. The tie-break is
    ``created_at`` (immutable), NOT ``updated_at`` — ``updated_at`` is bumped by
    unrelated writes (e.g. ``PUT /{run_id}/status``, and the demote UPDATE), so
    keying on it could rank an old, touched run above a newer compute.
    """
    stmt = (
        select(RebalancingRun.id)
        .where(RebalancingRun.user_id == user_id, committed_run_filter())
        .order_by(
            case((RebalancingRun.origin == ORIGIN_SAVED, 1), else_=0).desc(),
            RebalancingRun.created_at.desc(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
