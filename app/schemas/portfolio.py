"""Pydantic schema — `portfolio.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PortfolioResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    total_value: float = 0
    total_invested: float = 0
    total_gain_percentage: Optional[float] = None
    is_primary: bool = True
    created_at: datetime
    updated_at: datetime


class PortfolioDetailResponse(PortfolioResponse):
    allocations: list[PortfolioAllocationResponse] = []
    holdings: list[PortfolioHoldingResponse] = []


class PortfolioAllocationCreate(BaseModel):
    asset_class: str
    allocation_percentage: float = Field(..., ge=0, le=100)
    amount: float = 0


class PortfolioAllocationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    asset_class: str
    allocation_percentage: float
    amount: float
    performance_percentage: Optional[float] = None


class PortfolioAllocationBulkUpdate(BaseModel):
    total_investment: Optional[float] = None
    allocations: list[PortfolioAllocationCreate]


class PortfolioHoldingResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    instrument_name: str
    instrument_type: str
    ticker_symbol: Optional[str] = None
    quantity: Optional[float] = None
    average_cost: Optional[float] = None
    current_price: Optional[float] = None
    current_value: float
    allocation_percentage: Optional[float] = None
    exchange: Optional[str] = None
    expense_ratio: Optional[float] = None
    return_1y: Optional[float] = None
    return_3y: Optional[float] = None
    return_5y: Optional[float] = None


class PortfolioHistoryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    recorded_date: date
    total_value: float


class RecommendedPlanSnapshotResponse(BaseModel):
    """Latest persisted ideal allocation snapshot (``portfolio_allocation_snapshots``)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    snapshot_kind: str
    allocation: dict[str, Any]
    effective_at: datetime
    source: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime


class RecommendedPlanResponse(BaseModel):
    """Latest AI ideal plan for the current user (for dashboards / rebalancing UI)."""

    snapshot: Optional[RecommendedPlanSnapshotResponse] = None
    latest_rebalancing_id: Optional[uuid.UUID] = None
