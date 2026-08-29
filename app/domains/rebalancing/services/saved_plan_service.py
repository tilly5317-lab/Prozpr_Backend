"""Save + read the customer's committed rebalancing plan (spec 2026-08-27).

v1 is a status flip on ``rebalancing_runs.origin``: exactly one run per user
carries ``origin='saved'``. No tilt capture, no CAMS survival — those are v2.
"""

from __future__ import annotations

import uuid

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rebalancing.models.rebalancing_run import RebalancingRun


async def save_plan(
    db: AsyncSession, *, user_id: uuid.UUID, run_id: uuid.UUID
) -> RebalancingRun | None:
    """Mark one run as the user's committed plan (``origin='saved'``).

    Demotes any other saved run for the user so exactly one stays committed.
    Returns the run, or ``None`` if it does not exist / is not this user's.
    Does NOT commit — the caller owns the transaction.
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
            RebalancingRun.origin == "saved",
            RebalancingRun.id != run_id,
        )
        .values(origin=None)
    )
    run.origin = "saved"
    await db.flush()
    return run


async def select_current_run_id(
    db: AsyncSession, *, user_id: uuid.UUID
) -> uuid.UUID | None:
    """The run the portfolio page shows: the committed (saved) run if any,
    else the most-recently-created run. ``None`` if the user has no runs.

    ``case`` (not ``origin == 'saved'`` directly) keeps NULL origins sorting
    after saved regardless of the DB's NULL-ordering rules. The tie-break is
    ``created_at`` (immutable), NOT ``updated_at`` — ``updated_at`` is bumped by
    unrelated writes (e.g. ``PUT /{run_id}/status``, and the demote UPDATE), so
    keying on it could rank an old, touched run above a newer compute in the
    no-saved-plan fallback.
    """
    stmt = (
        select(RebalancingRun.id)
        .where(RebalancingRun.user_id == user_id)
        .order_by(
            case((RebalancingRun.origin == "saved", 1), else_=0).desc(),
            RebalancingRun.created_at.desc(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
