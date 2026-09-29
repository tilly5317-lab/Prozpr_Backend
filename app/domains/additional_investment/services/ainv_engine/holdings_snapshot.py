"""Load the current-holdings snapshot for the deficit-fill lumpsum path (spec 2026-07-03).

The snapshot model and its pure aggregation live in
``app.domains.portfolio.services.holdings_snapshot``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding
from app.domains.portfolio.services.holdings_snapshot import (
    HoldingsSnapshot,
    snapshot_from_holdings,
)

__all__ = ["HoldingsSnapshot", "load_holdings_snapshot"]


async def load_holdings_snapshot(
    db: AsyncSession, user_id: uuid.UUID
) -> HoldingsSnapshot:
    """Load + classify the user's holdings across their portfolios.

    ``fund_metadata`` joins on scheme_code (see the PortfolioHolding
    relationship); rows without metadata fall back to the instrument name so
    name-based classification overrides still get a chance."""
    stmt = (
        select(PortfolioHolding)
        .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user_id)
        .options(selectinload(PortfolioHolding.fund_metadata))
    )
    holdings = (await db.execute(stmt)).scalars().all()
    return snapshot_from_holdings(holdings)
