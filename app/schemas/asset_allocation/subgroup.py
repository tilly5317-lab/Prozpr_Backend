"""Asset-allocation subgroup schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AssetAllocationBucketSubgroupResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    bucket_id: uuid.UUID
    user_id: uuid.UUID
    subgroup: str
    planned_amount: float
    actual_amount: float
    planned_pct_of_bucket: Optional[float] = None
    actual_pct_of_bucket: Optional[float] = None
    created_at: datetime
