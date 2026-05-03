"""Chart tool — current asset allocation donut.

Reads PortfolioAllocation rows for a user's primary portfolio and returns a
CurrentAllocationDonut payload. Returns None when the user has no portfolio
or no allocation rows yet, so the caller can decide to skip emitting a chart.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioAllocation
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.schema import (
    CurrentAllocationDonut,
    DonutSlice,
)


async def build_current_allocation_donut(
    db: AsyncSession, user_id: uuid.UUID
) -> CurrentAllocationDonut | None:
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioAllocation)
        .where(PortfolioAllocation.portfolio_id == portfolio.id)
        .order_by(PortfolioAllocation.allocation_percentage.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None

    slices = [
        DonutSlice(
            label=row.asset_class,
            value=float(row.amount),
            percentage=float(row.allocation_percentage),
        )
        for row in rows
    ]
    total_value = sum(s.value for s in slices)

    return CurrentAllocationDonut(
        title="Current Asset Allocation",
        total_value=total_value,
        slices=slices,
    )
