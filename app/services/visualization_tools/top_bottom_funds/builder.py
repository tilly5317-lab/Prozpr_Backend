"""Chart builder — top-3 + bottom-3 funds by 1Y return.

Reads ``PortfolioHolding.return_1y`` from the user's primary portfolio. Skips
holdings without a 1Y return value (they get excluded from the average too).
Returns None if the user has no portfolio or fewer than 2 valued holdings.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioHolding
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.top_bottom_funds.schema import (
    FundReturnRow,
    TopBottomFunds,
)

_TOP_N = 3
_BOTTOM_N = 3


async def build_top_bottom_funds(
    db: AsyncSession, user_id: uuid.UUID
) -> TopBottomFunds | None:
    """Build the top/bottom-funds payload, or None if data missing."""
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio.id)
        .where(PortfolioHolding.return_1y.isnot(None))
        .order_by(PortfolioHolding.return_1y.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if len(rows) < 2:
        return None

    avg = sum(float(r.return_1y) for r in rows) / len(rows)

    top_rows = rows[:_TOP_N]
    bottom_rows = rows[-_BOTTOM_N:] if len(rows) > _TOP_N else []
    # If top + bottom would overlap, trim the overlap from bottom.
    top_set = {r.id for r in top_rows}
    bottom_rows = [r for r in bottom_rows if r.id not in top_set]

    def _row(h: PortfolioHolding) -> FundReturnRow:
        return FundReturnRow(
            name=h.instrument_name,
            return_pct=float(h.return_1y),
            current_value=float(h.current_value),
        )

    return TopBottomFunds(
        title="Best and worst performers",
        subtitle="1-year return per fund",
        top=[_row(r) for r in top_rows],
        bottom=[_row(r) for r in bottom_rows],
        portfolio_average_pct=avg,
    )
