"""Application service — `notification_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    message: str,
    notification_type: str,
    action_url: str | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        action_url=action_url,
    )
    db.add(notification)
    await db.flush()
    return notification
