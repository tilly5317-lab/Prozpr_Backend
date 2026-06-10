"""Pydantic response / request schemas for the rebalancing endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


# ── Nested child schemas ────────────────────────────────────────────────


class RebalancingTotalsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_buy_inr: float
    total_sell_inr: float
    net_cash_flow_inr: float
    total_stcg_realised: float
    total_ltcg_realised: float
    total_stcg_net_off: float
    total_tax_estimate_inr: float
    total_exit_load_inr: float
    unrebalanced_remainder_inr: float
    rows_count: int
    funds_to_buy_count: int
    funds_to_sell_count: int
    funds_to_exit_count: int
    funds_held_count: int


class RebalancingSubgroupSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_subgroup: str
    goal_target_inr: float
    current_holding_inr: float
    suggested_final_holding_inr: float
    rebalance_inr: float
    total_buy_inr: float
    total_sell_inr: float
    ranks_total: int
    ranks_with_holding: int
    ranks_with_action: int


class RebalancingTradeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    isin: str
    recommended_fund: str
    asset_subgroup: str
    sub_category: str
    action: str
    amount_inr: float
    reason_code: str
    reason_title: str
    reason_text: str
    execution_status: str
    executed_at: Optional[datetime] = None
    broker_ref: Optional[str] = None


class RebalancingWarningSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    message: str
    affected_isins: List[str]


# ── Top-level response schemas ──────────────────────────────────────────


class RebalancingRunListItem(BaseModel):
    """Light listing row — no eager-loaded children."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    portfolio_id: uuid.UUID
    source_allocation_run_id: uuid.UUID
    status: str
    engine_version: str
    tax_regime: str
    total_corpus: float
    created_at: datetime
    updated_at: datetime


class RebalancingRunDetailResponse(BaseModel):
    """Full detail with eager-loaded totals, subgroups, trades, and warnings."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    portfolio_id: uuid.UUID
    chat_session_id: Optional[uuid.UUID] = None
    source_allocation_run_id: uuid.UUID
    supersedes_id: Optional[uuid.UUID] = None
    status: str
    executed_at: Optional[datetime] = None

    engine_request_id: uuid.UUID
    engine_version: str
    computed_at: datetime

    tax_regime: str
    effective_tax_rate_pct: float
    total_corpus: float
    rounding_step: int

    stcg_offset_budget_inr: Optional[float] = None
    carryforward_st_loss_inr: float
    carryforward_lt_loss_inr: float

    user_question: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    totals: Optional[RebalancingTotalsSchema] = None
    subgroup_summaries: List[RebalancingSubgroupSummarySchema] = []
    trades: List[RebalancingTradeSchema] = []
    warnings: List[RebalancingWarningSchema] = []


# ── Readiness gate ──────────────────────────────────────────────────────


class RebalancingReadinessField(BaseModel):
    """One input the rebalancing engine needs, mirroring the cashflow gate."""

    key: str
    label: str
    group: str
    kind: str  # "date" | "money" | "int" | "percent"
    unit: Optional[str] = None
    help: Optional[str] = None
    optional: bool = False
    present: bool = False
    value: Optional[object] = None


class RebalancingReadinessResponse(BaseModel):
    """Whether the user has everything the rebalancing engine requires.

    ``ready`` is true only when every required field is present *and* the user
    has mutual-fund holdings. ``has_holdings`` is surfaced separately so the UI
    can show a "connect your portfolio" CTA rather than a form field.
    """

    ready: bool
    missing: List[str]
    fields: List[RebalancingReadinessField]
    has_holdings: bool


# ── Request schemas ─────────────────────────────────────────────────────


class RebalancingStatusUpdate(BaseModel):
    status: str


# Readiness gate schemas live in their own module; re-export for convenience so
# routers can import everything from ``app.domains.rebalancing.schemas``.
from app.domains.rebalancing.schemas.readiness import (  # noqa: E402
    RebalancingReadinessField,
    RebalancingReadinessResponse,
)

__all__ = [
    "RebalancingRunListItem",
    "RebalancingRunDetailResponse",
    "RebalancingStatusUpdate",
    "RebalancingReadinessField",
    "RebalancingReadinessResponse",
]
