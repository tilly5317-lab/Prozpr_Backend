"""Post-ingest cache invalidation.

Runs after a CAS snapshot is activated. Every computed plan is snapshot-scoped
(``CasScoped``) so the read hook already hides the superseded runs; what is NOT
scoped is the single ``portfolios`` row whose ``total_value`` the allocation
input builder reads as the corpus. Re-marking that row under the new snapshot is
the load-bearing step — the run deletes are belt-and-braces.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import cas_scope_for_user
from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.portfolio.services.portfolio_service import (
    revalue_primary_portfolio_at_latest_nav,
)
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun

logger = logging.getLogger(__name__)


async def invalidate_user_caches(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Clear computed plans and re-mark the primary portfolio to the active snapshot.

    Deletes are unfiltered on purpose (the read hook only scopes SELECTs), so
    every snapshot's runs go — the new snapshot has none yet. They run inside a
    SAVEPOINT: a failure here must never roll back the ingest that called us.
    """
    summary = {
        "user_id": str(user_id),
        "allocations_cleared": 0,
        "rebalancing_runs_cleared": 0,
        "portfolio_total_value": None,
        "error": None,
    }

    try:
        async with db.begin_nested():
            # rebalancing_runs.source_allocation_run_id is ON DELETE RESTRICT,
            # so the dependents must go first.
            rebal = await db.execute(
                delete(RebalancingRun).where(RebalancingRun.user_id == user_id)
            )
            alloc = await db.execute(
                delete(AssetAllocationRun).where(AssetAllocationRun.user_id == user_id)
            )
        summary["rebalancing_runs_cleared"] = rebal.rowcount or 0
        summary["allocations_cleared"] = alloc.rowcount or 0
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the ingest
        logger.warning("run cache clear failed for %s: %s", user_id, exc)
        summary["error"] = str(exc)

    try:
        async with cas_scope_for_user(db, user_id):
            portfolio = await revalue_primary_portfolio_at_latest_nav(db, user_id)
        if portfolio is not None:
            summary["portfolio_total_value"] = float(portfolio.total_value or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("portfolio revalue failed for %s: %s", user_id, exc)
        summary["error"] = str(exc)

    logger.info("cache invalidation %s", summary)
    return summary
