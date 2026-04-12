"""Pydantic schema — `linked_account.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LinkAccountRequest(BaseModel):
    account_type: str = Field(..., pattern="^(mutual_fund|bank_account|stock_demat|other)$")
    provider_name: Optional[str] = None


class LinkAccountResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    account_type: str
    provider_name: Optional[str] = None
    status: str
    linked_at: Optional[datetime] = None
    created_at: datetime


class LinkAccountListResponse(BaseModel):
    accounts: list[LinkAccountResponse]
