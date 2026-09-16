"""Chat core — `types.py`.

Orchestrates a single user turn: intent classification, branch routing (market, portfolio query, portfolio-style spine with liquidity gate and allocation), optional telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM user context from ``get_ai_user_context``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User


@dataclass(frozen=True)
class ChatTurnInput:
    """
    Everything needed for one assistant turn: session, question, history,
    optional client hints, and the ORM user graph preloaded for AI modules.
    """

    user_ctx: User
    user_question: str
    # Entries are {role, content, intent, asked_at} — asked_at is a datetime,
    # so this is not a str-valued dict. See chat_context.load_conversation_history.
    conversation_history: list[dict[str, Any]]
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
    asset_allocation_run_id: uuid.UUID | None = None
    ideal_allocation_rebalancing_id: uuid.UUID | None = None
    ideal_allocation_snapshot_id: uuid.UUID | None = None
    additional_investment_run_id: uuid.UUID | None = None
    # Cadence of that run ("sip_monthly" | "lumpsum"), for chat "View plan" routing.
    additional_investment_cadence: str | None = None
    # True when the turn produced a savable candidate preference (a what-if);
    # the rebalancing pill reflects that saving also keeps the preference.
    has_candidate_preference: bool = False
    # True when the turn should route the customer to their investment
    # preferences — they asked to change/clear the saved record, or named
    # something we could not map. Chat never WRITES a preference.
    show_preferences_pill: bool = False
    chart_payloads: list[dict[str, Any]] | None = None
    # True when the turn was answered by the "no holdings imported yet" guard
    # (see ``ai_engine.portfolio_gate``) — the UI shows an add-CAMS CTA.
    portfolio_data_missing: bool = False
