"""Pydantic DTOs for the benchmarks domain (catalogue + EOD values)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkIndexValueCreate(BaseModel):
    """Write payload for one daily EOD value row."""

    value_date: date
    tri_value: float = Field(..., gt=0)
    ntr_value: Optional[float] = Field(None, gt=0)
    pr_value: Optional[float] = Field(None, gt=0)


class BenchmarkValueRow(BaseModel):
    """One daily EOD value point returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    value_date: date
    tri_value: float
    ntr_value: Optional[float] = None
    pr_value: Optional[float] = None


class BenchmarkIndexSummary(BaseModel):
    """Catalogue row + its latest available EOD value (for listings)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str
    short_name: str
    provider: str
    asset_class: str
    description: Optional[str] = None
    earliest_available: Optional[date] = None
    is_active: bool
    latest_value: Optional[float] = None
    latest_value_date: Optional[date] = None
    created_at: datetime


class BenchmarkHistoryResponse(BaseModel):
    """A benchmark's EOD value series over a date range."""

    code: str
    display_name: str
    points: List[BenchmarkValueRow]
