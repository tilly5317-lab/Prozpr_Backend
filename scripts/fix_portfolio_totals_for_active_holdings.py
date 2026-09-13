#!/usr/bin/env python3
"""Fix portfolio totals to only include active holdings from the current CAS snapshot.

When users upload multiple CAMS statements, old PortfolioHolding rows from previous
uploads get marked with older cas_upload_id values. The portfolio.total_invested and
total_gain_percentage calculations were including ALL holdings regardless of snapshot,
resulting in wrong values.

This script recalculates these totals from only the active snapshot's holdings for
each user.
"""

import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import cas_scope_for_user, get_scope, set_scope
from app.core.database import _get_session_factory
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def fix_portfolio_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[bool, str]:
    """Recalculate portfolio totals for one user from active holdings only.

    Saves the corrected values to the database so they're used by other services
    (like asset allocation) that read portfolio.total_value.

    Returns (success, message).
    """
    try:
        # Load portfolio with all holdings (ORM hook filters to active snapshot)
        stmt = select(Portfolio).where(
            Portfolio.user_id == user_id,
            Portfolio.is_primary == True  # noqa: E712
        )
        portfolio = (await db.execute(stmt)).scalar_one_or_none()
        if not portfolio:
            return False, f"No primary portfolio for user {user_id}"

        # Get active holdings (should be filtered by scope)
        holdings_stmt = select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio.id
        )
        result = await db.execute(holdings_stmt)
        holdings = result.scalars().all()

        # Recalculate totals from active holdings only
        total_value = Decimal(0)
        total_invested = Decimal(0)

        for h in holdings:
            if h.current_value:
                total_value += Decimal(str(h.current_value))

            if h.average_cost and h.quantity and float(h.quantity or 0) > 0:
                total_invested += Decimal(str(h.average_cost)) * Decimal(str(h.quantity))

        # Calculate gain percentage
        if total_invested > 0:
            gain_pct = (total_value - total_invested) / total_invested * 100
            gain_pct = round(float(gain_pct), 2)
        else:
            gain_pct = None

        # Update portfolio AND PERSIST TO DATABASE
        # This is critical - the asset allocation engine reads portfolio.total_value
        # from the DB when building allocation input, so we must save here
        portfolio.total_value = float(total_value)
        portfolio.total_invested = float(total_invested)
        portfolio.total_gain_percentage = gain_pct

        await db.flush()  # Ensure changes are written
        await db.commit()

        return True, (
            f"Fixed portfolio for {user_id}: value={total_value}, "
            f"invested={total_invested}, gain={gain_pct}%"
        )
    except Exception as e:
        await db.rollback()
        return False, f"Error fixing portfolio for {user_id}: {e}"


async def main():
    """Fix portfolios for all users."""
    factory = _get_session_factory()

    # Get list of all users with primary portfolios
    async with factory() as db:
        users_stmt = text(
            "SELECT DISTINCT p.user_id FROM portfolios p "
            "WHERE p.is_primary = TRUE ORDER BY p.user_id"
        )
        result = await db.execute(users_stmt)
        user_ids = result.scalars().all()

    logger.info(f"Fixing portfolio totals for {len(user_ids)} users...")
    fixed = 0
    failed = 0

    for user_id in user_ids:
        async with factory() as db:
            # Set the CAS scope for this user to ensure we load the active snapshot
            async with cas_scope_for_user(db, user_id):
                success, message = await fix_portfolio_for_user(db, user_id)
                if success:
                    fixed += 1
                    logger.info(f"✓ {message}")
                else:
                    failed += 1
                    logger.warning(f"✗ {message}")

    logger.info(f"Done: {fixed} fixed, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
