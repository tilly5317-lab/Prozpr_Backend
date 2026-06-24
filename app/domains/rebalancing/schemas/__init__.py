"""Pydantic response / request schemas for the rebalancing endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, computed_field

from app.domains.mutual_funds.services.scheme_classification import (
    asset_class_for_subgroup,
)
from app.domains.rebalancing.services.rebalancing_summary import (
    build_rebalance_summary,
)


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def asset_class(self) -> str:
        """Canonical asset_class (Equity / Debt / Others) derived from asset_subgroup."""
        return asset_class_for_subgroup(self.asset_subgroup)


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def asset_class(self) -> str:
        """Canonical asset_class (Equity / Debt / Others) derived from asset_subgroup."""
        return asset_class_for_subgroup(self.asset_subgroup)


class RebalancingWarningSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    message: str
    affected_isins: List[str]


class RebalancingSummarySchema(BaseModel):
    """Plan-aware headline (title + one-line subtitle) for the run.

    Replaces the Invest page's old static "Time to fine-tune your mix." header
    with copy that reflects what this run actually recommends. Computed on read
    from the run's totals + per-asset-class drift — see ``rebalancing_summary``.
    """

    title: str  # what we're doing
    subtitle: str  # how / the numbers
    reason: Optional[str] = None  # one-line why; None when nothing needs justifying


class AssetClassBreakdownRow(BaseModel):
    """One Equity/Debt/Others row of the Invest-page current-vs-target bars."""

    asset_class: str  # "Equity" | "Debt" | "Others"
    current_inr: float
    target_inr: float


class RebalancingAssetClassBreakdown(BaseModel):
    """Backend-computed asset-class split for the Invest "Current vs Target" view.

    Blended multi-asset / hybrid funds are split per-category (look-through) so the
    frontend renders these numbers directly without any client-side classification.
    ``current`` mirrors the dashboard donut (from holdings); ``target`` reconciles
    with the trade list (from the rebalancing plan's per-subgroup totals).
    """

    rows: List[AssetClassBreakdownRow]
    current_total_inr: float
    target_total_inr: float


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
    # Populated by the run-detail router (read-time, not persisted): the
    # multi-asset-aware Equity/Debt/Others split for the Current-vs-Target bars.
    asset_class_breakdown: Optional[RebalancingAssetClassBreakdown] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> Optional[RebalancingSummarySchema]:
        """Personalized headline derived from this run's totals + drift direction."""
        result = build_rebalance_summary(
            self.totals,
            self.subgroup_summaries,
            self.trades,
            self.asset_class_breakdown,
        )
        if result is None:
            return None
        return RebalancingSummarySchema(
            title=result.title, subtitle=result.subtitle, reason=result.reason
        )


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
