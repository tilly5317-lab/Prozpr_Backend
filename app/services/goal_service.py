"""Application service — `goal_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goals import FinancialGoal


async def get_user_goals(db: AsyncSession, user_id: uuid.UUID) -> list[FinancialGoal]:
    stmt = (
        select(FinancialGoal)
        .where(FinancialGoal.user_id == user_id)
        .order_by(FinancialGoal.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def calculate_goal_progress(
    goal: FinancialGoal, *, tracked_value: float | None = None
) -> float:
    target = float(getattr(goal, "present_value_amount", 0) or 0)
    if target <= 0:
        return 0.0
    cur = float(tracked_value) if tracked_value is not None else 0.0
    return min(100.0, (cur / target) * 100)
