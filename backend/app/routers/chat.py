"""FastAPI router — `chat.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.models.chat import ChatMessage, ChatMessageRole, ChatSession, ChatSessionStatus
from app.models.chat_ai_module_run import ChatAiModuleRun
from app.models.user import User
from app.schemas.chat import (
    ChatAiModuleRunResponse,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSendMessageResponse,
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    UploadStatementResponse,
)
from app.services.chat_core import ChatBrain, ChatTurnInput
from app.services.chat_context import load_conversation_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.get(
    "/sessions/{session_id}/module-runs",
    response_model=list[ChatAiModuleRunResponse],
)
async def list_session_ai_module_runs(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    limit: int = 50,
):
    """AILAX audit: which modules ran for this chat turn sequence (also logged as AILAX_AI_MODULE_RUN)."""
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == current_user.id
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    runs_stmt = (
        select(ChatAiModuleRun)
        .where(ChatAiModuleRun.session_id == session_id)
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(min(limit, 200))
    )
    rows = (await db.execute(runs_stmt)).scalars().all()
    return [ChatAiModuleRunResponse.model_validate(r) for r in rows]


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.created_at.desc())
    )
    result = await db.execute(stmt)
    return [ChatSessionResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    session = ChatSession(
        user_id=current_user.id,
        title=payload.title or "New conversation",
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
    stmt = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

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
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == current_user.id
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    if session.status == ChatSessionStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This chat session is closed. Please start a new session.",
        )

    conversation_history = await load_conversation_history(session_id, db)

    user_msg = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.user,
        content=payload.content,
    )
    db.add(user_msg)

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

    assistant_msg = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.assistant,
        content=brain_result.content,
    )
    db.add(assistant_msg)

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    assistant_response = ChatMessageResponse.model_validate(assistant_msg)
    assistant_response.intent = brain_result.intent
    assistant_response.intent_confidence = brain_result.intent_confidence
    assistant_response.intent_reasoning = brain_result.intent_reasoning

    return ChatSendMessageResponse(
        user_message=ChatMessageResponse.model_validate(user_msg),
        assistant_message=assistant_response,
        ideal_allocation_rebalancing_id=brain_result.ideal_allocation_rebalancing_id,
        ideal_allocation_snapshot_id=brain_result.ideal_allocation_snapshot_id,
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == current_user.id
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    await db.delete(session)
    await db.commit()


@router.post(
    "/sessions/{session_id}/upload-statement",
    response_model=UploadStatementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_statement(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """
    Upload an investment statement file during onboarding or review.
    The raw content is stored as a user message in the chat session
    so downstream AI processing can incorporate it.
    """
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == current_user.id
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    raw_bytes = await file.read()
    raw_text = raw_bytes.decode(errors="ignore")

    msg = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.user,
        content=f"[Uploaded statement: {file.filename}]\n\n{raw_text}",
    )
    db.add(msg)
    await db.commit()

    return UploadStatementResponse(
        session_id=session_id,
        message="Statement uploaded successfully and will be incorporated into your profile.",
    )
