"""Pydantic schema — `finvu.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


BucketName = Literal["Cash", "Debt", "Equity", "Other"]


class FinvuBucketInput(BaseModel):
    bucket: BucketName
    value_inr: float = Field(..., ge=0, description="Absolute INR value in this bucket")


class FinvuPortfolioSyncRequest(BaseModel):
    """Payload after consent: post-normalised bucket totals (e.g. from Finvu analytics API)."""

    buckets: list[FinvuBucketInput] = Field(..., min_length=1)
    as_of: Optional[datetime] = None
    consent_transaction_id: Optional[str] = Field(
        default=None,
        description="Optional AA consent / fetch reference for audit",
    )
    source: str = Field(default="finvu", max_length=64)


class FinvuPortfolioSyncResponse(BaseModel):
    portfolio_id: str
    total_value_inr: float
    allocation_rows_written: int
    message: str
