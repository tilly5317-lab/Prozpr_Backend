"""Application service — `user_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.profile import PersonalFinanceProfile


async def get_or_create_profile(db: AsyncSession, user_id: uuid.UUID) -> PersonalFinanceProfile:
    stmt = select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = PersonalFinanceProfile(user_id=user_id)
        db.add(profile)
        await db.flush()
    return profile


async def update_user_fields(db: AsyncSession, user_id: uuid.UUID, **kwargs) -> User | None:
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return None
    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user
