"""Per-turn context bundle: history + last AgentRun per module + active intent.

Built once per chat turn from ``ChatTurnInput``. Consumed by ChatBrain
routing and downstream handlers (e.g. asset_allocation_followup).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_ai_module_run import ChatAiModuleRun
from app.models.user import User
from app.services.chat_core.types import ChatTurnInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunRecord:
    """Frozen view of one persisted chat_ai_module_runs row used by handlers."""
    id: uuid.UUID
    module: str
    intent_detected: str | None
    input_payload: dict[str, Any] | None
    output_payload: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class TurnContext:
    """Everything a handler needs about the current turn + session history."""
    user_ctx: User
    user_question: str
    conversation_history: list[dict[str, str]]
    client_context: dict[str, Any] | None
    session_id: uuid.UUID
    db: AsyncSession | None
    effective_user_id: uuid.UUID
    last_agent_runs: dict[str, AgentRunRecord]
    active_intent: str | None


async def build_turn_context(turn: ChatTurnInput) -> TurnContext:
    """Load last AgentRun per module + last intent_detected for this session.

    Failures degrade to empty context (the chat turn still works, just without
    follow-up routing capability).
    """
    last_runs: dict[str, AgentRunRecord] = {}
    active_intent: str | None = None

    if turn.db is not None and turn.session_id is not None:
        try:
            last_runs = await _load_last_agent_runs(turn.db, turn.session_id)
            active_intent = await _load_active_intent(turn.db, turn.session_id)
        except Exception as exc:
            logger.warning("build_turn_context degraded (%s); using empty context", exc)

    return TurnContext(
        user_ctx=turn.user_ctx,
        user_question=turn.user_question,
        conversation_history=turn.conversation_history,
        client_context=turn.client_context,
        session_id=turn.session_id,
        db=turn.db,
        effective_user_id=turn.effective_user_id,
        last_agent_runs=last_runs,
        active_intent=active_intent,
    )


async def _load_last_agent_runs(
    db: AsyncSession, session_id: uuid.UUID,
) -> dict[str, AgentRunRecord]:
    """One row per module — the most recent with output_payload populated.

    Implementation note: avoids Postgres-specific ``DISTINCT ON`` so the
    query works on both Postgres (production) and SQLite (local dev). The
    row volume per session is bounded by the small number of agents we
    persist runs for, so fetching all and deduping in Python is fine.
    """
    stmt = (
        select(ChatAiModuleRun)
        .where(ChatAiModuleRun.session_id == session_id)
        .where(ChatAiModuleRun.output_payload.isnot(None))
        .order_by(ChatAiModuleRun.module, ChatAiModuleRun.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    last_by_module: dict[str, AgentRunRecord] = {}
    for r in rows:
        if r.module in last_by_module:
            continue  # already kept the most recent for this module
        last_by_module[r.module] = AgentRunRecord(
            id=r.id,
            module=r.module,
            intent_detected=r.intent_detected,
            input_payload=r.input_payload,
            output_payload=r.output_payload,
            created_at=r.created_at,
        )
    return last_by_module


async def _load_active_intent(
    db: AsyncSession, session_id: uuid.UUID,
) -> str | None:
    """Most-recent intent_detected for this session, regardless of module."""
    stmt = (
        select(ChatAiModuleRun.intent_detected)
        .where(ChatAiModuleRun.session_id == session_id)
        .where(ChatAiModuleRun.intent_detected.isnot(None))
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
