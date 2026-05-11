"""Asset-allocation run schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.schemas.asset_allocation.aggregate import AssetAllocationAggregateResponse
from app.schemas.asset_allocation.bucket import AssetAllocationBucketResponse


class AssetAllocationRunResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    portfolio_id: Optional[uuid.UUID] = None
    chat_session_id: Optional[uuid.UUID] = None
    supersedes_id: Optional[uuid.UUID] = None
    status: str
    pipeline_source: str
    spine_mode: Optional[str] = None
    user_question: Optional[str] = None
    rationale: Optional[str] = None
    client_age: int
    client_occupation: Optional[str] = None
    client_effective_risk_score: float
    total_corpus: float
    grand_total: float
    all_amounts_in_multiples_of_100: bool
    created_at: datetime
    updated_at: datetime


class AssetAllocationRunDetailResponse(AssetAllocationRunResponse):
    buckets: list[AssetAllocationBucketResponse] = []
    aggregates: list[AssetAllocationAggregateResponse] = []
    input_payload: dict[str, Any] = {}


class AssetAllocationRunRequest(BaseModel):
    question: str
    portfolio_id: Optional[uuid.UUID] = None
    chat_session_id: Optional[uuid.UUID] = None


class AssetAllocationRunCreatedResponse(BaseModel):
    answer_markdown: str
    run_id: uuid.UUID
