"""Cross-turn state for the planning conversation.

One module for the two things that have to survive between turns: the question
PI has open (and what it is holding, unwritten, until the customer agrees), and
the goal being built. They were separate stores under the old split intents,
and keeping them apart is what forced the two gates to negotiate with each
other over who owned a bare "yes".

Commit-free by design — the chat router owns the transaction (``run_turn``
never commits), so every write here has to survive being rolled back with the
turn that made it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.financial_planning.models.chat_goal_draft import (
    STAGE_ABANDONED,
    STAGE_COLLECTING,
    STAGE_CONFIRMING,
    STAGE_FOLLOW_UP,
    ChatGoalDraft,
)
from app.domains.financial_planning.models.chat_planning_ask import (
    STATUS_ANSWERED,
    STATUS_CANCELLED,
    STATUS_CONFIRMING,
    STATUS_PENDING,
    STATUS_SKIPPED,
    ChatPlanningAsk,
)
from app.domains.financial_planning.models.planning_write import PlanningWrite

logger = logging.getLogger(__name__)

# Hard ceiling per conversation on questions the customer did NOT answer.
#
# Answered asks are deliberately excluded. The budget exists to stop unprompted
# nagging, not to cut someone off mid-flow: a projection needs four inputs, so
# counting every ask would make it impossible to unblock in one conversation
# even for a customer answering everything. Three ignored or declined questions
# is where a chat stops feeling like a conversation and starts feeling like a
# form.
MAX_ASKS_PER_SESSION = 3

# How long "not now" lasts, per user rather than per session — declining on
# Monday and being asked again on Tuesday is exactly the nagging this prevents.
DEFER_DAYS = 30

# Re-asks of the SAME field before we give up on it for this session.
MAX_ATTEMPTS = 2

# Stages that mean the goal conversation is still going. FOLLOW_UP is included
# on purpose: the goal is saved, but we have just asked whether anything else
# changed, and "no, everything's the same" is a bare fragment that classifies
# as anything at all. It belongs to this thread.
_OPEN_STAGES = (STAGE_COLLECTING, STAGE_CONFIRMING, STAGE_FOLLOW_UP)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The open question
# ---------------------------------------------------------------------------


async def get_pending(
    db: AsyncSession, session_id: uuid.UUID
) -> ChatPlanningAsk | None:
    """The open question for this session, if any."""
    return (
        await db.execute(
            select(ChatPlanningAsk)
            .where(ChatPlanningAsk.session_id == session_id)
            .where(ChatPlanningAsk.status.in_((STATUS_PENDING, STATUS_CONFIRMING)))
            .order_by(ChatPlanningAsk.asked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def asks_this_session(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Questions in this session the customer has not answered."""
    return int(
        (
            await db.execute(
                select(func.count(ChatPlanningAsk.id))
                .where(ChatPlanningAsk.session_id == session_id)
                .where(ChatPlanningAsk.status != STATUS_ANSWERED)
            )
        ).scalar_one()
        or 0
    )


async def deferred_field_keys(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """Fields the customer declined that are still inside their quiet period."""
    rows = (
        await db.execute(
            select(ChatPlanningAsk.field_key)
            .where(ChatPlanningAsk.user_id == user_id)
            .where(ChatPlanningAsk.deferred_until.isnot(None))
            .where(ChatPlanningAsk.deferred_until > _now())
        )
    ).scalars()
    return set(rows)


async def hard_declined_keys(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """Fields declined twice or more — never hard-blocked on again."""
    rows = (
        await db.execute(
            select(ChatPlanningAsk.field_key)
            .where(ChatPlanningAsk.user_id == user_id)
            .where(ChatPlanningAsk.status == STATUS_SKIPPED)
            .group_by(ChatPlanningAsk.field_key)
            .having(func.count(ChatPlanningAsk.id) >= 2)
        )
    ).scalars()
    return set(rows)


async def open_ask(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    field_key: str,
    resume_intent: str | None,
    ask_kind: str = "hard",
    origin_question: str | None = None,
) -> ChatPlanningAsk:
    """Record that PI is now asking about ``field_key``.

    Any previously open question is cancelled first: two open questions at once
    is the state that makes an extractor mis-attribute an answer.
    """
    existing = await get_pending(db, session_id)
    if existing is not None:
        existing.status = STATUS_CANCELLED
        existing.resolved_at = _now()

    run = ChatPlanningAsk(
        session_id=session_id,
        user_id=user_id,
        field_key=field_key,
        resume_intent=resume_intent,
        origin_question=(origin_question or None),
        status=STATUS_PENDING,
        ask_kind=ask_kind,
    )
    db.add(run)
    await db.flush()
    return run


async def mark_confirming(db: AsyncSession, run: ChatPlanningAsk) -> None:
    """Values are staged and read back; waiting for the customer to agree."""
    run.status = STATUS_CONFIRMING
    await db.flush()


async def mark_answered(db: AsyncSession, run: ChatPlanningAsk) -> None:
    run.status = STATUS_ANSWERED
    run.resolved_at = _now()
    await db.flush()


async def mark_skipped(
    db: AsyncSession, run: ChatPlanningAsk, *, days: int = DEFER_DAYS
) -> None:
    run.status = STATUS_SKIPPED
    run.resolved_at = _now()
    run.deferred_until = _now() + timedelta(days=days)
    await db.flush()


async def bump_attempt(db: AsyncSession, run: ChatPlanningAsk) -> None:
    """The customer replied but nothing usable came out. Give up after
    ``MAX_ATTEMPTS`` rather than asking the same thing forever."""
    run.attempts = (run.attempts or 0) + 1
    if run.attempts >= MAX_ATTEMPTS:
        run.status = STATUS_CANCELLED
        run.resolved_at = _now()
    await db.flush()


async def stage_values(
    db: AsyncSession, run: ChatPlanningAsk, staged: dict[str, Any]
) -> None:
    """Hold what we understood, unwritten.

    Reassigned rather than mutated: SQLAlchemy does not track in-place edits to
    a JSONB dict, so a mutated staging area silently would not persist.
    """
    run.staged_values = dict(staged) if staged else None
    await db.flush()


# ---------------------------------------------------------------------------
# The audit trail (and undo)
# ---------------------------------------------------------------------------


async def record_write(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    ask_id: uuid.UUID | None,
    field_key: str,
    table_name: str,
    column_name: str,
    previous: Any,
    value: Any,
    source: str,
    confidence: float | None = None,
    verbatim: str | None = None,
) -> PlanningWrite:
    """Audit row. Values are JSON-encoded, so dates go in as ISO strings and a
    whole deleted goal round-trips as an object."""
    row = PlanningWrite(
        user_id=user_id,
        session_id=session_id,
        capture_run_id=ask_id,
        field_key=field_key[:64],
        table_name=table_name,
        column_name=column_name,
        previous_value=jsonable(previous),
        new_value=jsonable(value),
        source=source,
        confidence=confidence,
        verbatim=(verbatim or None),
    )
    db.add(row)
    await db.flush()
    return row


async def last_undoable_write(
    db: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID
) -> PlanningWrite | None:
    return (
        await db.execute(
            select(PlanningWrite)
            .where(PlanningWrite.user_id == user_id)
            .where(PlanningWrite.session_id == session_id)
            .where(PlanningWrite.undone_at.is_(None))
            .order_by(PlanningWrite.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def writes_in_turn(
    db: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID, since: datetime
) -> list[PlanningWrite]:
    """Everything written in this session since ``since`` — the undo scope for a
    turn that changed several things at once."""
    rows = (
        await db.execute(
            select(PlanningWrite)
            .where(PlanningWrite.user_id == user_id)
            .where(PlanningWrite.session_id == session_id)
            .where(PlanningWrite.undone_at.is_(None))
            .where(PlanningWrite.created_at >= since)
            .order_by(PlanningWrite.created_at.desc())
        )
    ).scalars()
    return list(rows)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# The goal being built
# ---------------------------------------------------------------------------


async def get_open_draft(
    db: AsyncSession, session_id: uuid.UUID
) -> ChatGoalDraft | None:
    """The goal currently being built in this session, if any."""
    return (
        await db.execute(
            select(ChatGoalDraft)
            .where(ChatGoalDraft.session_id == session_id)
            .where(ChatGoalDraft.stage.in_(_OPEN_STAGES))
            .order_by(ChatGoalDraft.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_latest_draft(
    db: AsyncSession, session_id: uuid.UUID
) -> ChatGoalDraft | None:
    """The most recent draft whatever its stage — used for the post-commit
    follow-up, where the draft is already committed."""
    return (
        await db.execute(
            select(ChatGoalDraft)
            .where(ChatGoalDraft.session_id == session_id)
            .order_by(ChatGoalDraft.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_draft(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    origin_question: str | None = None,
    editing_goal_id: uuid.UUID | None = None,
) -> ChatGoalDraft:
    """Start a draft. Any earlier open draft in this session is abandoned — two
    goals half-built at once is the state that makes an extractor attach an
    answer to the wrong one.

    ``editing_goal_id`` marks the draft as a re-costing of a goal that already
    exists, so committing it UPDATES that row rather than adding a second one.
    """
    existing = await get_open_draft(db, session_id)
    if existing is not None:
        existing.stage = STAGE_ABANDONED

    draft = ChatGoalDraft(
        session_id=session_id,
        user_id=user_id,
        stage=STAGE_COLLECTING,
        slots=({"editing_goal_id": str(editing_goal_id)} if editing_goal_id else {}),
        origin_question=(origin_question or None),
    )
    db.add(draft)
    await db.flush()
    return draft


async def update_draft_slots(
    db: AsyncSession, draft: ChatGoalDraft, slots: dict[str, Any]
) -> None:
    draft.slots = dict(slots)
    await db.flush()


async def set_draft_projection(
    db: AsyncSession, draft: ChatGoalDraft, projection: dict[str, Any]
) -> None:
    draft.projection = dict(projection)
    await db.flush()


async def set_draft_stage(
    db: AsyncSession, draft: ChatGoalDraft, stage: str
) -> None:
    draft.stage = stage
    await db.flush()


async def mark_draft_committed(
    db: AsyncSession, draft: ChatGoalDraft, goal_id: uuid.UUID
) -> None:
    """The goal is written; the thread stays open for the one follow-up."""
    draft.stage = STAGE_FOLLOW_UP
    draft.committed_goal_id = goal_id
    await db.flush()


__all__ = [
    "DEFER_DAYS",
    "MAX_ASKS_PER_SESSION",
    "MAX_ATTEMPTS",
    "asks_this_session",
    "bump_attempt",
    "create_draft",
    "deferred_field_keys",
    "get_latest_draft",
    "get_open_draft",
    "get_pending",
    "hard_declined_keys",
    "jsonable",
    "last_undoable_write",
    "mark_answered",
    "mark_confirming",
    "mark_draft_committed",
    "mark_skipped",
    "open_ask",
    "record_write",
    "set_draft_projection",
    "set_draft_stage",
    "stage_values",
    "update_draft_slots",
    "writes_in_turn",
]
