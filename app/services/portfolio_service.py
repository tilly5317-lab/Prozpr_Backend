"""Application service — `portfolio_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import Portfolio


async def get_primary_portfolio(db: AsyncSession, user_id: uuid.UUID) -> Portfolio | None:
    stmt = select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.is_primary == True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_or_create_primary_portfolio(db: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    portfolio = await get_primary_portfolio(db, user_id)
    if not portfolio:
        portfolio = Portfolio(user_id=user_id, name="Primary", is_primary=True)
        db.add(portfolio)
        await db.flush()
    return portfolio
