"""Application service — `chat_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage


async def load_conversation_history(
    session_id: uuid.UUID, db: AsyncSession
) -> list[dict[str, str]]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(stmt)
    return [
        {"role": msg.role.value, "content": msg.content}
        for msg in result.scalars().all()
    ]
