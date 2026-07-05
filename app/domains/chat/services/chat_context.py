"""Application service — `chat_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.chat.models.chat import ChatMessage


# Cap history sent to LLM prompts. 20 messages ≈ 10 turns of recent context —
# enough for natural follow-ups, bounded so long-lived sessions don't overflow
# the context window or balloon token cost.
_HISTORY_DEFAULT_LIMIT = 20

# Per-message char cap for LLM history. Every prompt history block (intent
# classifier, per-module action detectors, answer formatter) is built from this
# function's output, so this is the single chokepoint that bounds prompt size.
# 24,000 chars (~6K tokens) is above anything written legitimately — user
# messages are schema-capped at 8,000 chars and even the longest formatter
# replies (goal-planning tables, ~6K output tokens) fit — so this only fires on
# rows that bypassed the write-path caps. Display paths read messages directly
# from the ORM and are unaffected.
_MAX_MESSAGE_CHARS = 24_000
_TRUNCATION_MARKER = " …[truncated]"


def _capped(content: str) -> str:
    if len(content) <= _MAX_MESSAGE_CHARS:
        return content
    return content[:_MAX_MESSAGE_CHARS] + _TRUNCATION_MARKER


async def load_conversation_history(
    session_id: uuid.UUID,
    db: AsyncSession,
    *,
    limit: int = _HISTORY_DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    """Return the most recent ``limit`` messages for this session in chronological order.

    Each entry is ``{"role", "content", "intent"}`` — ``intent`` is the turn's
    classified intent on assistant rows (None on user rows and pre-column
    history). Prompt builders read only role/content; the intent classifier's
    history scrub keys on ``intent``.
    """
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
        {"role": msg.role.value, "content": _capped(msg.content), "intent": msg.intent}
        for msg in rows
    ]
