"""Chat HTTP routes — session CRUD and message send."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.domains.chat.models.chat import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
    ChatSessionStatus,
)
from app.domains.chat.models.chat_ai_module_run import ChatAiModuleRun
from app.domains.identity.models.user import User
from app.domains.chat.schemas.chat import (
    ChatAiModuleRunResponse,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSendMessageResponse,
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionRatingUpdate,
    ChatSessionResponse,
    ChatSessionUpdate,
)
from app.domains.ai_engine import ChatBrain, ChatTurnInput
from app.domains.chat.services.chat_context import load_conversation_history
from app.domains.chat.services.chat_title_service import generate_chat_title

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


# ---------------------------------------------------------------------------
# Helper: look up a session owned by the current user.
# ---------------------------------------------------------------------------


async def _get_user_session(
    session_id: uuid.UUID,
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    load_messages: bool = False,
) -> ChatSession:
    """Fetch a session or raise 404. Optionally eager-load messages."""
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == user_id
    )
    if load_messages:
        stmt = stmt.options(selectinload(ChatSession.messages))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )
    return session


# ---------------------------------------------------------------------------
# Endpoints (order matters: /sessions/active must precede /sessions/{id})
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/module-runs", response_model=list[ChatAiModuleRunResponse]
)
async def list_session_ai_module_runs(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    limit: int = 50,
):
    """Prozpr audit trail for a session's AI module invocations."""
    await _get_user_session(session_id, db, current_user.id)
    rows = (
        (
            await db.execute(
                select(ChatAiModuleRun)
                .where(ChatAiModuleRun.session_id == session_id)
                .order_by(ChatAiModuleRun.created_at.desc())
                .limit(min(limit, 200))
            )
        )
        .scalars()
        .all()
    )
    return [ChatAiModuleRunResponse.model_validate(r) for r in rows]


@router.get("/sessions/active", response_model=ChatSessionDetailResponse)
async def get_or_create_active_session(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Return the user's single persistent chat session, creating one if needed."""
    stmt = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(
            ChatSession.user_id == current_user.id,
            ChatSession.status == ChatSessionStatus.active,
        )
        .order_by(ChatSession.created_at.desc())
        .limit(1)
    )
    session = (await db.execute(stmt)).scalar_one_or_none()

    if not session:
        session = ChatSession(user_id=current_user.id, title="New Chat")
        db.add(session)
        await db.commit()
        await db.refresh(session, attribute_names=["messages"])

    return ChatSessionDetailResponse(
        **ChatSessionResponse.model_validate(session).model_dump(),
        messages=[ChatMessageResponse.model_validate(m) for m in session.messages],
    )


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """List all sessions for the authenticated user (newest first)."""
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.created_at.desc())
    )
    return [ChatSessionResponse.model_validate(s) for s in result.scalars().all()]


@router.post(
    "/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED
)
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Create a new chat session."""
    session = ChatSession(
        user_id=current_user.id, title=payload.title or "New conversation"
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return ChatSessionResponse.model_validate(session)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Fetch a single session with its full message history."""
    session = await _get_user_session(
        session_id, db, current_user.id, load_messages=True
    )
    return ChatSessionDetailResponse(
        **ChatSessionResponse.model_validate(session).model_dump(),
        messages=[ChatMessageResponse.model_validate(m) for m in session.messages],
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatSendMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    session_id: uuid.UUID,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
):
    """Send a user message, run the AI brain, and return both messages."""
    session = await _get_user_session(session_id, db, current_user.id)

    if session.status == ChatSessionStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This chat session is closed.",
        )

    conversation_history = await load_conversation_history(session_id, db)

    # First-turn auto-title: start the Haiku call CONCURRENTLY with the brain
    # so titling adds no latency to the reply (it used to block for up to 6s
    # after the brain finished). The intent hint is dropped — it isn't known
    # yet, and the message text dominates the title anyway. Only on the very
    # first message and while the title is still a default, so a manual or
    # earlier title is never overwritten. generate_chat_title never raises.
    title_task: asyncio.Task | None = None
    if not conversation_history and (session.title or "") in (
        "",
        "New Chat",
        "New conversation",
        "Pi Chat",
    ):
        title_task = asyncio.create_task(
            generate_chat_title(payload.content, intent_name=None)
        )

    # Persist user message.
    user_msg = ChatMessage(
        session_id=session_id, role=ChatMessageRole.user, content=payload.content
    )
    db.add(user_msg)

    # Run the AI brain.
    brain_result = await ChatBrain().run_turn(
        ChatTurnInput(
            user_ctx=user_ctx,
            user_question=payload.content,
            conversation_history=conversation_history,
            client_context=payload.client_context,
            session_id=session_id,
            db=db,
            user_id=current_user.id,
        )
    )

    # Re-add user message in case the brain rolled back the transaction.
    insp = sa_inspect(user_msg)
    if insp.detached or insp.transient:
        db.add(user_msg)

    # Persist assistant reply, tagged with the turn's classified intent so the
    # classifier's history scrub can identify canned-redirect turns by tag
    # (not by matching message text).
    assistant_msg = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.assistant,
        content=brain_result.content,
        intent=brain_result.intent,
    )
    db.add(assistant_msg)

    # Collect the auto-title started before the brain (usually done by now, so
    # this await is ~free). Best-effort — the generator never raises
    # (deterministic fallback), and on a session detached by a brain rollback
    # this simply won't persist, which is fine.
    if title_task is not None:
        session.title = await title_task

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    assistant_response = ChatMessageResponse.model_validate(assistant_msg)
    assistant_response.intent = brain_result.intent
    assistant_response.intent_confidence = brain_result.intent_confidence
    assistant_response.intent_reasoning = brain_result.intent_reasoning
    assistant_response.chart_payloads = brain_result.chart_payloads

    return ChatSendMessageResponse(
        user_message=ChatMessageResponse.model_validate(user_msg),
        assistant_message=assistant_response,
        asset_allocation_run_id=brain_result.asset_allocation_run_id,
        ideal_allocation_snapshot_id=brain_result.ideal_allocation_snapshot_id,
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: uuid.UUID,
    payload: ChatSessionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Rename a chat session. Once renamed, first-turn auto-titling won't run
    (the title is no longer a default), so a manual name is never overwritten."""
    session = await _get_user_session(session_id, db, current_user.id)
    session.title = payload.title.strip()
    await db.commit()
    await db.refresh(session)
    return ChatSessionResponse.model_validate(session)


@router.patch("/sessions/{session_id}/rating", response_model=ChatSessionResponse)
async def rate_session(
    session_id: uuid.UUID,
    payload: ChatSessionRatingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Record the user's 1–5 rating of Pi for this session.

    Idempotent — one rating per session; calling again overwrites it. The
    frontend reads this back on load so the rating prompt is shown only until
    the conversation has been rated, not on every revisit.
    """
    session = await _get_user_session(session_id, db, current_user.id)
    session.rating = payload.rating
    await db.commit()
    await db.refresh(session)
    return ChatSessionResponse.model_validate(session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Delete a chat session and all its messages."""
    session = await _get_user_session(session_id, db, current_user.id)
    await db.delete(session)
    await db.commit()


