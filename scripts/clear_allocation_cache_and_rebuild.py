#!/usr/bin/env python3
"""Clear stale allocation caches and force recalculation from active holdings.

Deletes cached asset allocation runs and rebalancing recommendations to force
fresh calculations based on the current active CAS snapshot. This ensures
all users get plans calculated from their ACTIVE holdings only, not archived
funds from previous uploads.
"""

import asyncio
import logging
import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import cas_scope_for_user
from app.core.database import _get_session_factory
from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
from app.domains.portfolio.models.portfolio import Portfolio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def clear_caches_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[int, int]:
    """Delete cached allocations and rebalancing runs for one user.

    Returns (allocations_deleted, rebalancing_runs_deleted).
    """
    try:
        # Delete rebalancing runs (these reference allocation runs)
        rebal_result = await db.execute(
            delete(RebalancingRun).where(RebalancingRun.user_id == user_id)
        )
        rebal_deleted = rebal_result.rowcount or 0

        # Delete asset allocation runs (the cached plans)
        alloc_result = await db.execute(
            delete(AssetAllocationRun).where(
                AssetAllocationRun.user_id == user_id
            )
        )
        alloc_deleted = alloc_result.rowcount or 0

        await db.commit()
        return alloc_deleted, rebal_deleted
    except Exception as e:
        await db.rollback()
        logger.error(f"Error clearing caches for {user_id}: {e}")
        return 0, 0


async def main():
    """Clear caches for all users with portfolios."""
    factory = _get_session_factory()

    # Get list of all users with primary portfolios
    async with factory() as db:
        users_stmt = text(
            "SELECT DISTINCT p.user_id FROM portfolios p "
            "WHERE p.is_primary = TRUE ORDER BY p.user_id"
        )
        result = await db.execute(users_stmt)
        user_ids = result.scalars().all()

    logger.info(f"Clearing allocation caches for {len(user_ids)} users...")
    total_allocs = 0
    total_rebals = 0
    failed = 0

    for user_id in user_ids:
        async with factory() as db:
            # Set the CAS scope for this user to ensure proper context
            async with cas_scope_for_user(db, user_id):
                allocs, rebals = await clear_caches_for_user(db, user_id)
                total_allocs += allocs
                total_rebals += rebals

                status = f"Deleted: {allocs} allocations, {rebals} rebalancing runs"
                if allocs > 0 or rebals > 0:
                    logger.info(f"✓ {user_id}: {status}")
                else:
                    logger.debug(f"  {user_id}: {status}")

    logger.info(
        f"Done: Deleted {total_allocs} allocation caches and {total_rebals} "
        f"rebalancing runs for {len(user_ids)} users"
    )
    logger.info(
        "Next rebalancing request will calculate fresh plans from active holdings only"
    )


if __name__ == "__main__":
    asyncio.run(main())
