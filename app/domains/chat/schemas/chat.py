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
    # The persisted rebalancing run the assistant just presented, so the client
    # can offer "Save this plan" → POST /rebalancing/{run_id}/save. The frontend
    # already declares/reads this exact name; today the backend just never sent it.
    ideal_allocation_rebalancing_id: Optional[uuid.UUID] = None
    ideal_allocation_snapshot_id: Optional[uuid.UUID] = None
    # The question needed the user's holdings and none are imported yet — the
    # reply asks for a CAS statement, and the client shows an upload CTA.
    portfolio_data_missing: bool = False
    # The session's title after this turn. Only ever differs from the previous
    # one on the first turn, when the auto-titler replaces the placeholder —
    # the client uses it to repaint the session list without a refetch.
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
