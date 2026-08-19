"""Per-turn context bundle: history + last AgentRun per module + active intent.

Built once per chat turn from ``ChatTurnInput``. Consumed by ChatBrain
routing and downstream handlers (e.g. asset_allocation_chat).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.chat.models.chat_ai_module_run import ChatAiModuleRun
from app.domains.identity.models.user import User
from app.domains.ai_engine.chat_types import ChatTurnInput

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
    conversation_history: list[dict[str, Any]]
    client_context: dict[str, Any] | None
    session_id: uuid.UUID
    db: AsyncSession | None
    effective_user_id: uuid.UUID
    last_agent_runs: dict[str, AgentRunRecord]
    active_intent: str | None
    chat_overrides: dict[str, Any] | None = None
    # Data the classifier says this answer needs (intent_classifier Tool values).
    # Attached by the brain after classification; handlers fetch only what is
    # listed. Empty means the customer's own record is enough.
    tools_needed: tuple[str, ...] = ()
    # VESTIGIAL — always False. Nothing has set the save gate since the AA/
    # rebalancing "save it" follow-up was removed; the per-turn DB load and the
    # brain's routing override were deleted in the 2026-07 audit (F11). The
    # field survives only so existing TurnContext constructors keep working;
    # remove it together with ChatSessionState if durable save is abandoned.
    awaiting_save: bool = False
    # asyncio.Task running the module's follow-up action detector, started by
    # the brain concurrently with the intent classifier and attached (via
    # dataclasses.replace) ONLY when the classified intent matches
    # active_intent. Handlers consume it through
    # chat_dispatcher.consume_speculative_detect; None → serial detect.
    speculative_detect: Any = None
    # ``planning_gate.PlanningDirective`` when this turn belongs to financial
    # planning — an open question, a goal half-built, or an input another intent
    # cannot run without. Attached by the brain (via dataclasses.replace)
    # immediately before it routes to flow_financial_planning; None on every
    # other turn.
    planning_directive: Any = None


async def build_turn_context(turn: ChatTurnInput) -> TurnContext:
    """Load last AgentRun per module + last intent_detected for this session.

    Failures degrade to empty context (the chat turn still works, just without
    follow-up routing capability).
    """
    last_runs: dict[str, AgentRunRecord] = {}
    active_intent: str | None = None

    if turn.db is not None and turn.session_id is not None:
        # Use a savepoint so a failed query (e.g. schema behind ORM, missing columns)
        # does not call Session.rollback(), which expires all instances and breaks
        # async SQLAlchemy (lazy loads → MissingGreenlet on user.portfolios, etc.).
        try:
            async with turn.db.begin_nested():
                last_runs = await _load_last_agent_runs(turn.db, turn.session_id)
                active_intent = await _load_active_intent(turn.db, turn.session_id)
        except Exception:
            # ERROR level + stack trace so silent quality regressions (PI
            # answers everyone like it's their first turn) surface in alerts.
            logger.exception(
                "build_turn_context failed for session=%s; chat will run with EMPTY context — investigate",
                turn.session_id,
            )

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
        chat_overrides=None,
    )


async def _load_last_agent_runs(
    db: AsyncSession,
    session_id: uuid.UUID,
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
        # Skip stub rows with no payload (e.g. formatter telemetry rows that
        # share a module name with the engine but carry no allocation_result).
        # SQL `output_payload IS NOT NULL` does not catch JSON-text "null" on
        # SQLite, which deserializes to Python None — filter again here.
        if not r.output_payload:
            continue
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
    db: AsyncSession,
    session_id: uuid.UUID,
) -> str | None:
    """Most-recent intent_detected for this session, excluding canned-redirect intents.

    out_of_scope and stock_advice surface a canned redirect rather than engaging
    with the user's real topic. Feeding either back as active_intent biases the
    classifier to keep refusing/redirecting on the next turn, which mis-routes
    legitimate follow-ups. ``goal_planning`` and ``profile_update`` are here as
    RETIRED labels: rows written before the financial_planning merge still carry
    them, and the classifier can no longer emit either, so replaying one as
    active_intent would raise on the ``Intent`` lookup.

    ``financial_planning`` is deliberately NOT excluded. Unlike the intents it
    replaced it is a genuine topic and usually a multi-turn one — a goal being
    built, a figure being corrected — and carrying it forward is what lets a
    follow-up land in the same thread.
    """
    canned_redirect_intents = (
        "out_of_scope",
        "stock_advice",
        "goal_planning",
        "profile_update",
    )
    stmt = (
        select(ChatAiModuleRun.intent_detected)
        .where(ChatAiModuleRun.session_id == session_id)
        .where(ChatAiModuleRun.intent_detected.isnot(None))
        .where(ChatAiModuleRun.intent_detected.notin_(canned_redirect_intents))
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
