"""Application service — `user_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.portfolio import Portfolio
from app.models.user import User


async def load_user_for_ai(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = (
        select(User)
        .options(
            selectinload(User.personal_finance_profile),
            selectinload(User.risk_profile),
            selectinload(User.investment_profile),
            selectinload(User.financial_goals),
            selectinload(User.portfolios).selectinload(Portfolio.allocations),
        )
        .where(User.id == user_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
