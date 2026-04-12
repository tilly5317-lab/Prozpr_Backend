"""Pydantic schema — `ips.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class IPSResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    version: int
    status: str
    content: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class IPSListResponse(BaseModel):
    statements: list[IPSResponse]
