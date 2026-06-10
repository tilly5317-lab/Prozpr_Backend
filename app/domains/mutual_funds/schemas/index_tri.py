"""NSE index TRI rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class IndexTriHistoryCreate(BaseModel):
    index_name: str = Field(..., max_length=50)
    tri_date: date
    tri_value: float = Field(..., gt=0)
    ntr_value: Optional[float] = Field(None, gt=0)


class IndexTriHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    index_name: str
    tri_date: date
    tri_value: float
    ntr_value: Optional[float]
    created_at: datetime
