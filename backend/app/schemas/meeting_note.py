"""Pydantic schema — `meeting_note.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MeetingNoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    meeting_date: Optional[datetime] = None


class MeetingNoteUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    is_mandate_approved: Optional[bool] = None


class MeetingNoteResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    meeting_date: Optional[datetime] = None
    is_mandate_approved: bool = False
    created_at: datetime
    updated_at: datetime


class MeetingNoteDetailResponse(MeetingNoteResponse):
    items: list[MeetingNoteItemResponse] = []


class MeetingNoteItemCreate(BaseModel):
    item_type: str = Field(..., pattern="^(transcript|summary)$")
    role: Optional[str] = None
    content: str = Field(..., min_length=1)
    sort_order: int = 0


class MeetingNoteItemUpdate(BaseModel):
    content: Optional[str] = None
    sort_order: Optional[int] = None


class MeetingNoteItemResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    item_type: str
    role: Optional[str] = None
    content: str
    sort_order: int = 0
    created_at: datetime
