"""Pydantic schema — `rebalancing.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RebalancingResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    portfolio_id: uuid.UUID
    status: str
    recommendation_data: Optional[dict] = None
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RebalancingStatusUpdate(BaseModel):
    status: str
