"""Pydantic schema — `review.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ReviewPreferenceUpdate(BaseModel):
    frequency: Optional[str] = None
    triggers: Optional[list[str]] = None
    update_process: Optional[str] = None


class ReviewPreferenceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    frequency: Optional[str] = None
    triggers: Optional[list[str]] = None
    update_process: Optional[str] = None
    updated_at: Optional[datetime] = None
