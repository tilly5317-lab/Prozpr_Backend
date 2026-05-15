"""Asset-allocation aggregate schema."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AssetAllocationAggregateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    run_id: uuid.UUID
    user_id: uuid.UUID
    split_kind: str
    equity_amount: float
    debt_amount: float
    others_amount: float
    equity_pct: float
    debt_pct: float
    others_pct: float
    created_at: datetime
