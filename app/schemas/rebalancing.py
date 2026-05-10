"""Pydantic schemas for the rebalancing run API.

The DB stores the rebalancing engine output across many normalized tables.
These schemas are the API contract — keep them stable for the frontend.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RebalancingTotalsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_buy_inr: Decimal
    total_sell_inr: Decimal
    net_cash_flow_inr: Decimal
    total_stcg_realised: Decimal
    total_ltcg_realised: Decimal
    total_stcg_net_off: Decimal
    total_tax_estimate_inr: Decimal
    total_exit_load_inr: Decimal
    unrebalanced_remainder_inr: Decimal
    rows_count: int
    funds_to_buy_count: int
    funds_to_sell_count: int
    funds_to_exit_count: int
    funds_held_count: int


class RebalancingTradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    isin: str
    recommended_fund: str
    asset_subgroup: str
    sub_category: str
    action: str
    amount_inr: Decimal
    reason_code: str
    reason_title: str
    reason_text: str
    execution_status: str


class RebalancingSubgroupSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_subgroup: str
    goal_target_inr: Decimal
    current_holding_inr: Decimal
    suggested_final_holding_inr: Decimal
    rebalance_inr: Decimal
    total_buy_inr: Decimal
    total_sell_inr: Decimal
    ranks_total: int
    ranks_with_holding: int
    ranks_with_action: int


class RebalancingWarningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    affected_isins: List[str]


class RebalancingRunListItem(BaseModel):
    """Lightweight list item — no fund-level audit attached."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    portfolio_id: uuid.UUID
    source_allocation_run_id: uuid.UUID
    status: str
    engine_version: str
    computed_at: datetime
    created_at: datetime
    updated_at: datetime


class RebalancingRunDetailResponse(BaseModel):
    """Full detail view — totals + subgroup roll-ups + trades + warnings."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    portfolio_id: uuid.UUID
    source_allocation_run_id: uuid.UUID
    status: str
    engine_version: str
    computed_at: datetime
    created_at: datetime
    updated_at: datetime

    totals: Optional[RebalancingTotalsResponse] = None
    subgroup_summaries: List[RebalancingSubgroupSummaryResponse] = []
    trades: List[RebalancingTradeResponse] = []
    warnings: List[RebalancingWarningResponse] = []


class RebalancingStatusUpdate(BaseModel):
    status: str
