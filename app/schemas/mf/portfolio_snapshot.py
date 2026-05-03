"""Portfolio allocation snapshots (JSONB)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import PortfolioSnapshotKind


class PortfolioAllocationSnapshotCreate(BaseModel):
    snapshot_kind: PortfolioSnapshotKind
    allocation: dict[str, Any] | list[Any]
    effective_at: Optional[datetime] = None
    source: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class PortfolioAllocationSnapshotUpdate(BaseModel):
    snapshot_kind: Optional[PortfolioSnapshotKind] = None
    allocation: Optional[dict[str, Any] | list[Any]] = None
    effective_at: Optional[datetime] = None
    source: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class PortfolioAllocationSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    snapshot_kind: PortfolioSnapshotKind
    allocation: dict[str, Any] | list[Any]
    effective_at: datetime
    source: Optional[str]
    notes: Optional[str]
    created_at: datetime
