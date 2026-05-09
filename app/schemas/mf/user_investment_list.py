"""User guardrail / watchlist JSONB lists."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import UserInvestmentListKind


class UserInvestmentListCreate(BaseModel):
    list_kind: UserInvestmentListKind
    entries: dict[str, Any] | list[Any] = Field(default_factory=list)


class UserInvestmentListUpdate(BaseModel):
    entries: dict[str, Any] | list[Any] | None = None


class UserInvestmentListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    list_kind: UserInvestmentListKind
    entries: dict[str, Any] | list[Any]
    created_at: datetime
    updated_at: datetime
