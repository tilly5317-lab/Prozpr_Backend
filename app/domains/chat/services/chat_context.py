"""Application service — `chat_context.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from itertools import groupby
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.chat.models.chat import (
    ChatMessage,
    ChatMessageRole,
    message_role_rank,
)


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
) -> list[dict[str, Any]]:
    """Return this session's recent COMPLETE turns, oldest first.

    Each entry is ``{"role", "content", "intent", "asked_at"}`` — ``intent`` is
    the turn's classified intent on assistant rows (None on user rows and
    pre-column history), ``asked_at`` the turn's timestamp, shared by both of
    its rows. Prompt builders read role/content; the intent classifier's history
    scrub keys on ``intent``; ``asked_at`` lets renderers tell a live thread
    from one resumed days later.

    A turn is the unit, not a message: both rows are written together, so an
    exchange is only meaningful as a pair. Half-turns are DROPPED — they arise
    when a turn errors mid-flight (the dev DB holds 42 user-only and 43
    assistant-only rows) or when ``limit`` cuts a pair at the window edge. That
    matters because a lone user message reads to an LLM as a live unanswered
    question: on 2026-07-25 one sent the portfolio_query agent down its
    goal-planning guardrail instead of answering the question actually asked.
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        # Both keys descending — the rows are reversed below, so this lands the
        # user row before its assistant row. See message_role_rank().
        .order_by(ChatMessage.created_at.desc(), message_role_rank().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()  # chronological so prompts read naturally

    # Both rows of a turn share created_at (one transaction, one func.now()),
    # and the query sorts on it, so each group is exactly one turn.
    history: list[dict[str, Any]] = []
    for asked_at, group in groupby(rows, key=lambda m: m.created_at):
        turn = list(group)
        question = next((m for m in turn if m.role is ChatMessageRole.user), None)
        answer = next((m for m in turn if m.role is ChatMessageRole.assistant), None)
        if question is None or answer is None:
            continue
        history.append(_entry(question, asked_at))
        history.append(_entry(answer, asked_at))
    return history


def _entry(msg: ChatMessage, asked_at: datetime) -> dict[str, Any]:
    return {
        "role": msg.role.value,
        "content": _capped(msg.content),
        "intent": msg.intent,
        "asked_at": asked_at,
    }
