"""Pydantic schemas for the "Talk to the Prozpr team" Zoom booking."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TeamCallCreateRequest(BaseModel):
    """Slot the user picked. ``start_time`` must be in the future."""

    start_time: datetime
    agenda: str = Field(default="", max_length=2000)


class TeamCallResponse(BaseModel):
    meeting_id: int
    join_url: str
    start_time: datetime
    duration_minutes: int
    topic: str
