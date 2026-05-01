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


class ChatSessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: list[ChatMessageResponse] = []


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
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
    ideal_allocation_rebalancing_id: Optional[uuid.UUID] = None
    ideal_allocation_snapshot_id: Optional[uuid.UUID] = None


class UploadStatementResponse(BaseModel):
    session_id: uuid.UUID
    message: str


class ChatAiModuleRunResponse(BaseModel):
    """One row from chat AI module telemetry (grep logs: AILAX_AI_MODULE_RUN)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    module: str
    reason: str
    intent_detected: Optional[str] = None
    spine_mode: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: Optional[dict[str, Any]] = None
    created_at: datetime
