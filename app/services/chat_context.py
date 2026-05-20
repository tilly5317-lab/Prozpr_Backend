"""Application service — `chat_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage


# Cap history sent to LLM prompts. 20 messages ≈ 10 turns of recent context —
# enough for natural follow-ups, bounded so long-lived sessions don't overflow
# the context window or balloon token cost.
_HISTORY_DEFAULT_LIMIT = 20


async def load_conversation_history(
    session_id: uuid.UUID,
    db: AsyncSession,
    *,
    limit: int = _HISTORY_DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    """Return the most recent ``limit`` messages for this session in chronological order."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()  # chronological so prompts read naturally
    return [
        {"role": msg.role.value, "content": msg.content}
        for msg in rows
    ]
