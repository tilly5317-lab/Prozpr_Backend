"""Chat HTTP routes — session CRUD and message send."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
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
from app.domains.ai_engine.streaming import open_token_stream
from app.domains.ai_engine.thinking import clear_thinking, get_thinking
from app.domains.chat.services.chat_context import load_conversation_history
from app.domains.chat.services.chat_title_service import maybe_start_auto_title
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatThinkingResponse(BaseModel):
    """Live thinking feed of this user's in-flight chat turn (polled while the
    send-message POST runs). ``messages`` is the full history so far (oldest
    first) so no line is missed between polls."""

    active: bool
    progress_pct: float
    message: str | None = None
    messages: list[str] = []


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


@router.get("/sessions/{session_id}/thinking", response_model=ChatThinkingResponse)
async def get_session_thinking(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_effective_user),
) -> ChatThinkingResponse:
    """Live "thinking aloud" line of this session's in-flight AI turn (if any).

    Deliberately no DB hit: the store is keyed by (effective user, session), so
    another user's session id can never read this user's feed, and the chat UI
    polls this every second while a reply is pending.
    """
    return ChatThinkingResponse(**get_thinking(current_user.id, session_id))


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

    # First-turn auto-title, run alongside the brain so it adds no latency.
    title_task = maybe_start_auto_title(
        current_title=session.title,
        has_history=bool(conversation_history),
        first_message=payload.content,
    )

    # Persist user message.
    user_msg = ChatMessage(
        session_id=session_id, role=ChatMessageRole.user, content=payload.content
    )
    db.add(user_msg)

    # Run the AI brain. It publishes live "thinking aloud" lines to the
    # per-session store as it works (polled via GET .../thinking); always
    # clear on the way out so the feed can never present as stuck active.
    try:
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
    finally:
        clear_thinking(current_user.id, session_id)

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
        portfolio_data_missing=brain_result.portfolio_data_missing,
        session_title=session.title,
    )


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_streaming(
    session_id: uuid.UUID,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
):
    """Same turn as ``send_message``, delivered as Server-Sent Events.

    Events: ``delta`` (incremental answer text once the answer LLM starts
    generating), then exactly one terminal ``done`` or ``error``. Pipeline stage
    lines do NOT ride this stream — they are polled from
    ``GET /chat/sessions/{id}/thinking``.

    ``done`` IS AUTHORITATIVE. Deltas are provisional — the formatter discards a
    truncated response in favour of a deterministic brief, and general_chat
    renders its answer field into a different final shape — so a client that has
    painted deltas must replace them with ``done.assistant_message.content``.

    Requires ``proxy_buffering off`` at nginx, and a single uvicorn worker: the
    token sink is in process memory (see ``ai_engine.streaming``).
    """
    session = await _get_user_session(session_id, db, current_user.id)
    if session.status == ChatSessionStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This chat session is closed.",
        )
    conversation_history = await load_conversation_history(session_id, db)

    async def events():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        user_msg = ChatMessage(
            session_id=session_id, role=ChatMessageRole.user, content=payload.content
        )
        db.add(user_msg)

        # First-turn auto-title, exactly as the non-streaming endpoint does
        # it. This endpoint is the one the app actually calls, so without
        # this a session kept its "New Chat" placeholder forever.
        title_task = maybe_start_auto_title(
            current_title=session.title,
            has_history=bool(conversation_history),
            first_message=payload.content,
        )

        try:
            async with open_token_stream() as stream:
                turn = asyncio.create_task(
                    ChatBrain().run_turn(
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
                )
                turn.add_done_callback(lambda _: stream.close())
                async for delta in stream:
                    yield sse("delta", {"text": delta})
                brain_result = await turn
        except Exception as exc:
            logger.exception("streaming chat turn failed (session=%s)", session_id)
            if title_task is not None:
                title_task.cancel()
            yield sse("error", {"detail": type(exc).__name__})
            return
        finally:
            clear_thinking(current_user.id, session_id)

        insp = sa_inspect(user_msg)
        if insp.detached or insp.transient:
            db.add(user_msg)
        assistant_msg = ChatMessage(
            session_id=session_id,
            role=ChatMessageRole.assistant,
            content=brain_result.content,
            intent=brain_result.intent,
        )
        db.add(assistant_msg)

        # Collect the auto-title started before the brain (usually already
        # done, so this await is ~free) and persist it in the same commit.
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
        yield sse(
            "done",
            ChatSendMessageResponse(
                user_message=ChatMessageResponse.model_validate(user_msg),
                assistant_message=assistant_response,
                asset_allocation_run_id=brain_result.asset_allocation_run_id,
                ideal_allocation_snapshot_id=brain_result.ideal_allocation_snapshot_id,
                portfolio_data_missing=brain_result.portfolio_data_missing,
                session_title=session.title,
            ).model_dump(),
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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


