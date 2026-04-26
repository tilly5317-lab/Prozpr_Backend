"""Chat core — `types.py`.

Orchestrates a single user turn: intent classification, branch routing (market, portfolio query, portfolio-style spine with liquidity gate and allocation), optional telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM user context from ``get_ai_user_context``.
"""


from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


@dataclass(frozen=True)
class ChatTurnInput:
    """
    Everything needed for one assistant turn: session, question, history,
    optional client hints, and the ORM user graph preloaded for AI modules.
    """

    user_ctx: User
    user_question: str
    conversation_history: list[dict[str, str]]
    client_context: dict[str, Any] | None
    session_id: uuid.UUID
    db: AsyncSession | None = None
    user_id: uuid.UUID | None = None

    @property
    def effective_user_id(self) -> uuid.UUID:
        return self.user_id or self.user_ctx.id


@dataclass
class ChatBrainResult:
    """Final assistant message plus intent metadata for the API / UI."""

    content: str
    intent: str | None
    intent_confidence: float | None
    intent_reasoning: str | None
    ideal_allocation_rebalancing_id: uuid.UUID | None = None
    ideal_allocation_snapshot_id: uuid.UUID | None = None
    chart_payloads: list[dict[str, Any]] | None = None
