"""Pydantic schema — `chat.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatSessionCreate(BaseModel):
    title: Optional[str] = None


class ChatSessionUpdate(BaseModel):
    """Patch a session's user-editable fields (currently just the title)."""

    title: str = Field(..., min_length=1, max_length=255)


class ChatSessionRatingUpdate(BaseModel):
    """Set the user's 1–5 star rating of Pi for a session (one per session)."""

    rating: int = Field(..., ge=1, le=5)


class ChatSessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: Optional[str] = None
    status: str
    # 1–5 if the user has rated this conversation, else null.
    rating: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: list[ChatMessageResponse] = []


class ChatMessageCreate(BaseModel):
    # 8000-char cap keeps any realistic user question well within prompt budget
    # and prevents abuse (huge pastes inflating LLM cost / prompt-injection surface).
    content: str = Field(..., min_length=1, max_length=8000)
    client_context: Optional[dict[str, Any]] = None


class ChatMessageResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    role: str
    content: str
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    intent_reasoning: Optional[str] = None
    chart_payloads: Optional[list[dict[str, Any]]] = None
    created_at: datetime


class ChatSendMessageResponse(BaseModel):
    """Returned by the send-message endpoint with both the user and assistant messages."""

    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    asset_allocation_run_id: Optional[uuid.UUID] = None
    ideal_allocation_snapshot_id: Optional[uuid.UUID] = None
    # The question needed the user's holdings and none are imported yet — the
    # reply asks for a CAS statement, and the client shows an upload CTA.
    portfolio_data_missing: bool = False
    # Plan inputs written on this turn; the client shows a saved chip + undo.
    # Each carries an optional ``basis`` when the value was worked out from one
    # we already held ("20% increase on the ₹30,00,000 on file").
    planning_saved: Optional[list[dict[str, Any]]] = None
    # A goal created or re-costed on this turn.
    goal_saved: Optional[dict[str, Any]] = None
    # Goals removed on this turn; the same undo puts them back.
    goal_removed: Optional[list[dict[str, Any]]] = None
    # The session's title after this turn. On the first turn that is the freshly
    # generated one, so the client can rename the conversation in its sidebar
    # straight away instead of waiting for the next session-list fetch.
    session_title: Optional[str] = None


class ChatAiModuleRunResponse(BaseModel):
    """One row from chat AI module telemetry (grep logs: PROZPR_AI_MODULE_RUN)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    module: str
    reason: str
    intent_detected: Optional[str] = None
    spine_mode: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: Optional[dict[str, Any]] = None
    created_at: datetime
