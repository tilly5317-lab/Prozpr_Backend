"""Daily NAV rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MfNavHistoryCreate(BaseModel):
    scheme_code: str = Field(..., max_length=20)
    isin: Optional[str] = Field(None, max_length=20)
    scheme_name: str = Field(..., max_length=200)
    mf_type: str = Field(..., max_length=200)
    nav: float = Field(..., gt=0)
    nav_date: date


class MfNavHistoryUpdate(BaseModel):
    isin: Optional[str] = Field(None, max_length=20)
    scheme_name: Optional[str] = Field(None, max_length=200)
    mf_type: Optional[str] = Field(None, max_length=200)
    nav: Optional[float] = Field(None, gt=0)
    nav_date: Optional[date] = None


class MfNavHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scheme_code: str
    isin: Optional[str]
    scheme_name: str
    mf_type: str
    nav: float
    nav_date: date
    created_at: datetime
