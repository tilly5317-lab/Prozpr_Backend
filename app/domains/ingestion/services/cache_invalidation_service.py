"""Cache invalidation service for CAMS ingestion.

Automatically clears stale caches after successful CAMS upload to ensure
allocations and rebalancing plans are recalculated with the new holdings data.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding

logger = logging.getLogger(__name__)


async def invalidate_user_caches(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Invalidate all caches and recalculate portfolio totals after CAMS upload.

    This should be called after successful CAMS ingestion to ensure:
    1. Allocation caches are cleared (will force recalculation)
    2. Rebalancing runs are cleared (will force recalculation)
    3. Portfolio totals are recalculated from active holdings only

    Returns a summary dict with counts of cleared/recalculated items.
    """
    try:
        summary = {
            "user_id": str(user_id),
            "allocations_cleared": 0,
            "rebalancing_runs_cleared": 0,
            "portfolio_recalculated": False,
            "error": None,
        }

        # 1. Clear allocation caches - forces next request to recalculate
        alloc_result = await db.execute(
            delete(AssetAllocationRun).where(
                AssetAllocationRun.user_id == user_id
            )
        )
        summary["allocations_cleared"] = alloc_result.rowcount or 0

        # 2. Clear rebalancing recommendations - forces next request to recalculate
        rebal_result = await db.execute(
            delete(RebalancingRun).where(RebalancingRun.user_id == user_id)
        )
        summary["rebalancing_runs_cleared"] = rebal_result.rowcount or 0

        # 3. Recalculate portfolio totals from active holdings only
        portfolio_stmt = select(Portfolio).where(
            Portfolio.user_id == user_id,
            Portfolio.is_primary == True  # noqa: E712
        )
        portfolio = (await db.execute(portfolio_stmt)).scalar_one_or_none()

        if portfolio:
            # Get active holdings (ORM hook filters to active CAS snapshot)
            holdings_stmt = select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio.id
            )
            result = await db.execute(holdings_stmt)
            holdings = result.scalars().all()

            # Recalculate totals from active holdings
            total_value = Decimal(0)
            total_invested = Decimal(0)

            for h in holdings:
                if h.current_value:
                    total_value += Decimal(str(h.current_value))

                if h.average_cost and h.quantity and float(h.quantity or 0) > 0:
                    total_invested += Decimal(str(h.average_cost)) * Decimal(
                        str(h.quantity)
                    )

            # Calculate gain percentage
            if total_invested > 0:
                gain_pct = (total_value - total_invested) / total_invested * 100
                gain_pct = round(float(gain_pct), 2)
            else:
                gain_pct = None

            # Update portfolio with corrected values
            portfolio.total_value = float(total_value)
            portfolio.total_invested = float(total_invested)
            portfolio.total_gain_percentage = gain_pct

            summary["portfolio_recalculated"] = True
            logger.info(
                f"Recalculated portfolio for {user_id}: "
                f"value={total_value}, invested={total_invested}, gain={gain_pct}%"
            )

        await db.commit()

        logger.info(
            f"Cache invalidation for {user_id}: "
            f"cleared {summary['allocations_cleared']} allocations, "
            f"{summary['rebalancing_runs_cleared']} rebalancing runs, "
            f"recalculated portfolio={summary['portfolio_recalculated']}"
        )

        return summary

    except Exception as e:
        await db.rollback()
        logger.error(f"Error invalidating caches for {user_id}: {e}", exc_info=True)
        return {
            "user_id": str(user_id),
            "allocations_cleared": 0,
            "rebalancing_runs_cleared": 0,
            "portfolio_recalculated": False,
            "error": str(e),
        }
