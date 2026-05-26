"""Pydantic schemas for ReviewPreference read/update."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ReviewPreferenceUpdate(BaseModel):
    frequency: Optional[str] = None
    triggers: Optional[list[Any]] = None
    update_process: Optional[str] = None


class ReviewPreferenceResponse(BaseModel):
    model_config = {"from_attributes": True}

    frequency: Optional[str] = None
    triggers: Optional[list[Any]] = None
    update_process: Optional[str] = None
