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


class AllocationSubCategoryResponse(BaseModel):
    """One SEBI sub-category's share of a single asset class."""

    name: str
    amount: float


class PortfolioAllocationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    asset_class: str
    allocation_percentage: float
    amount: float
    performance_percentage: Optional[float] = None
    # Sub-category breakdown of THIS asset class, for the donut's slice
    # drill-down. Blended funds are look-through split, so a hybrid appears
    # under each class it splits into and these amounts sum to `amount` above.
    # Do NOT rebuild this by grouping `holdings` on their single `asset_class` —
    # that ignores the split. Empty for Cash (no holding behind it).
    sub_categories: list[AllocationSubCategoryResponse] = []


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
    # Internal 4-bucket asset class (Equity / Debt / Cash / Other) computed from
    # fund_metadata + scheme name via resolve_asset_bucket. Matches the vocabulary
    # of PortfolioAllocation.asset_class so donut and holdings list agree.
    asset_class: Optional[str] = None
    # SEBI sub-category from MfFundMetadata (e.g. "Large Cap Fund", "Liquid Fund").
    sub_category: Optional[str] = None


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
    latest_asset_allocation_run_id: Optional[uuid.UUID] = None


class PortfolioNavHistoryPoint(BaseModel):
    model_config = {"from_attributes": True}

    recorded_date: date
    total_value: float
    total_invested: float
    gain_percentage: float


class PortfolioNavHistoryResponse(BaseModel):
    horizon: str
    points: list[PortfolioNavHistoryPoint]
    total_invested: float = 0
    current_value: float = 0
    gain_percentage: float = 0
    # Series metadata, straight off ``user_networth_series_state`` — one PK lookup,
    # not the COUNT/MAX scans this endpoint used to run on every dashboard load.
    as_of: Optional[date] = None
    is_stale: bool = False
    # Funds valued off a stated or stale NAV. Non-zero means the chart is real but
    # provisional, and the UI should say so rather than present it as settled.
    degraded_schemes: int = 0
    ledger_complete: bool = True
    # True when the series was downsampled for this horizon. Every point is still a
    # real stored day; there are just fewer of them than there are days.
    downsampled: bool = False


class NetworthJobStatusResponse(BaseModel):
    status: str  # none | pending | running | success | failed
    phase: Optional[str] = None
    progress_pct: float = 0
    message: Optional[str] = None
    history_from: Optional[date] = None
    days_total: Optional[int] = None
    has_history: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Degraded-data counters from the last build (stale prices, failed NAV fetches).
    warnings: Optional[dict[str, Any]] = None


class TwrPoint(BaseModel):
    date: date
    portfolio_index: float
    nifty_index: Optional[float] = None


class TwrSeriesResponse(BaseModel):
    has_data: bool  # True only when there are >= 2 valued days (renderable)
    points: list[TwrPoint]
    portfolio_xirr: Optional[float] = None
    as_of_date: Optional[date] = None
