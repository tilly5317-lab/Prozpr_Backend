"""In-chat planning endpoints — the two things the conversation cannot do itself.

Mounted under ``/chat`` because both act on a chat session rather than on the
profile screen.

  * **undo** — restores the previous value from the audit row, whether that
    value was a profile field or a whole goal. It is the one thing that makes
    writing to a financial record from a chat message safe: the customer can
    always take it back without having to remember what it was before.
  * **state** — the open question plus overall completeness, so a client can
    re-render after a reload without replaying the conversation.

There is deliberately no "answer" or "skip" endpoint. Pi asks in prose and the
customer answers in prose; a typed answer card rendered inside a conversation
is still a form, and the whole point of capturing here is that they never have
to fill one in.

Unlike ``run_turn``, these DO commit: they are their own request, with no turn
to ride along with.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.chat.models.chat import ChatSession
from app.domains.financial_planning.schemas import PlanningStateResponse, UndoResponse
from app.domains.financial_planning.services import (
    downstream,
    goal_ops,
    planning_audit as audit,
    planning_state as state,
    profile_ops,
)
from app.domains.identity.models.user import User
from app.domains.profile.services.profile_completeness_service import (
    completeness_for_user,
)
from app.domains.profile.services.profile_field_registry import spec
from app.domains.profile.services.profile_write_router import (
    FieldValidationError,
    restore_field,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


async def _owned_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> ChatSession:
    row = (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .where(ChatSession.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )
    return row


@router.post("/sessions/{session_id}/planning/undo", response_model=UndoResponse)
async def undo_planning_write(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    effective_user: User = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
) -> UndoResponse:
    """Roll back the most recent plan change made in this conversation."""
    await _owned_session(db, session_id, current_user.id)

    write = await state.last_undoable_write(
        db, user_id=effective_user.id, session_id=session_id
    )
    if write is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nothing to undo in this conversation",
        )

    label = write.field_key
    try:
        if write.table_name == "goals":
            if write.previous_value:
                # An edit or a deletion: put the goal back exactly as it was.
                goal = await goal_ops.restore(
                    db, effective_user.id, write.previous_value
                )
                label = goal.display_name
            else:
                # It was created here, so undoing it means removing it again.
                goals = await goal_ops.active_goals(db, effective_user.id)
                created = next(
                    (g for g in goals if str(g.id) == write.field_key), None
                )
                if created is not None:
                    label = created.display_name
                    await db.delete(created)
        else:
            await restore_field(
                db, effective_user.id, write.field_key, write.previous_value
            )
            fs = spec(write.field_key)
            label = profile_ops.short_label(fs) if fs else write.field_key
    except FieldValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    write.undone_at = datetime.now(timezone.utc)
    await db.flush()

    # Undoing a change is a change: the same effects that fired on the way in
    # have to fire on the way out, or the customer is left with a score or a
    # cached plan built on a value that no longer exists.
    report = await downstream.fire(
        db, effective_user.id, downstream.changes_from_writes([write])
    )
    await audit.log_undo(
        db, effective_user.id, session_id, write=write, report=report
    )

    await db.commit()
    return UndoResponse(
        field_key=write.field_key,
        label=label,
        restored_to=write.previous_value,
    )


@router.get("/sessions/{session_id}/planning/state", response_model=PlanningStateResponse)
async def planning_state(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    effective_user: User = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
) -> PlanningStateResponse:
    """What is open on this session, and how complete the customer's record is."""
    await _owned_session(db, session_id, current_user.id)

    ask = await state.get_pending(db, session_id)
    draft = await state.get_open_draft(db, session_id)
    pending_question = None
    if ask is not None:
        fs = spec(ask.field_key)
        if fs is not None:
            pending_question = fs.question

    return PlanningStateResponse(
        pending_question=pending_question,
        pending_field_key=(ask.field_key if ask is not None else None),
        goal_in_progress=(draft.stage if draft is not None else None),
        asks_used=await state.asks_this_session(db, session_id),
        asks_allowed=state.MAX_ASKS_PER_SESSION,
        completeness=await completeness_for_user(db, effective_user.id),
    )
