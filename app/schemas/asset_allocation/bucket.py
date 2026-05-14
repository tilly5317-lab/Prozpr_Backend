"""Asset-allocation bucket schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.asset_allocation.subgroup import AssetAllocationBucketSubgroupResponse


class AssetAllocationBucketResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    run_id: uuid.UUID
    bucket_name: str
    total_goal_amount: float
    allocated_amount: float
    rationale: Optional[str] = None
    future_investment_amount: float
    future_investment_message: Optional[str] = None
    created_at: datetime
    subgroups: list[AssetAllocationBucketSubgroupResponse] = []
