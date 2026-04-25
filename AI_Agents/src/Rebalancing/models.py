"""Pydantic models for the rebalancing engine.

Per-step `FundRowAfterStepN` models inherit from one another, so each step's
required fields are non-Optional and type-checked. Adding a column tomorrow:
add one field to the right `FundRowAfterStepN`, update one step. Inheritance
keeps later steps unchanged.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ── Per-step row models ──────────────────────────────────────────────────────


class FundRowInput(BaseModel):
    """Engine input row.

    One row per `(asset_subgroup, sub_category, recommended_fund, rank)` slot
    in the rank table. Held-but-not-recommended ("BAD") funds use `rank = 0`,
    `is_recommended = False`, `target_amount_pre_cap = 0`.
    """

    # Identity
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    rank: int = Field(ge=0)

    # Goal-allocation target (only rank-1 of each subgroup carries amount;
    # ranks 2+ start at 0 and may receive cap-spill in step 1).
    target_amount_pre_cap: Decimal = Field(ge=0)

    # Present-holding state (zero for not-yet-held funds)
    present_allocation_inr: Decimal = Field(default=Decimal(0), ge=0)
    invested_cost_inr: Decimal = Field(default=Decimal(0), ge=0)

    # Tax-aging breakdown of the present holding
    st_value_inr: Decimal = Field(default=Decimal(0), ge=0)
    st_cost_inr: Decimal = Field(default=Decimal(0), ge=0)
    lt_value_inr: Decimal = Field(default=Decimal(0), ge=0)
    lt_cost_inr: Decimal = Field(default=Decimal(0), ge=0)

    # Exit-load
    exit_load_pct: float = Field(default=0.0, ge=0.0)
    exit_load_months: int = Field(default=0, ge=0)
    units_within_exit_load_period: Decimal = Field(default=Decimal(0), ge=0)
    current_nav: Decimal = Field(default=Decimal(0), ge=0)

    # Status
    fund_rating: int = Field(default=10, ge=1, le=10)
    is_recommended: bool = True


class FundRowAfterStep1(FundRowInput):
    max_pct: float                       # cap that applies to this fund (% of corpus)
    target_pre_cap_pct: float              # original pre-cap target / corpus
    target_own_capped_pct: float              # post-own-cap, before spill received
    final_target_pct: float              # final after spill cascade
    final_target_amount: Decimal         # final rupees, rounded


class FundRowAfterStep2(FundRowAfterStep1):
    diff: Decimal                        # final_target_amount − present (signed)
    exit_flag: bool                      # forced exit (BAD or low-rated)
    worth_to_change: bool                # |diff| past threshold OR exit_flag


class FundRowAfterStep3(FundRowAfterStep2):
    stcg_amount: Decimal                 # st_value − st_cost (signed)
    ltcg_amount: Decimal                 # lt_value − lt_cost (signed)
    exit_load_amount: Decimal            # potential load if all in-period units sold


class FundRowAfterStep4(FundRowAfterStep3):
    pass1_buy_amount: Decimal
    pass1_underbuy_amount: Decimal
    pass1_sell_amount: Decimal
    pass1_undersell_amount: Decimal
    pass1_sell_lt_amount: Decimal
    pass1_realised_ltcg: Decimal
    pass1_sell_st_amount: Decimal
    pass1_realised_stcg: Decimal
    stcg_budget_remaining_after_pass1: Decimal
    pass1_sell_amount_no_stcg_cap: Decimal
    pass1_undersell_due_to_stcg_cap: Decimal
    pass1_blocked_stcg_value: Decimal
    holding_after_initial_trades: Decimal


class FundRowAfterStep5(FundRowAfterStep4):
    stcg_offset_amount: Decimal
    pass2_sell_amount: Decimal
    pass2_undersell_amount: Decimal
    final_holding_amount: Decimal


# ── Request / response wrappers ─────────────────────────────────────────────


class RebalancingComputeRequest(BaseModel):
    total_corpus: Decimal = Field(ge=0)
    tax_regime: Literal["old", "new"]
    effective_tax_rate_pct: float = Field(ge=0.0, le=100.0)
    rounding_step: int = Field(default=100, ge=1)

    # Per-request capital-gains state (bucket D)
    stcg_offset_budget_inr: Optional[Decimal] = None
    carryforward_st_loss_inr: Decimal = Field(default=Decimal(0), ge=0)
    carryforward_lt_loss_inr: Decimal = Field(default=Decimal(0), ge=0)

    # All rows: recommended (rank≥1) and BAD (rank=0)
    rows: list[FundRowInput]

    # Tracing
    request_id: UUID = Field(default_factory=uuid4)


class WarningCode(str, Enum):
    UNREBALANCED_REMAINDER = "UNREBALANCED_REMAINDER"
    BAD_FUND_DETECTED = "BAD_FUND_DETECTED"
    STCG_BUDGET_BINDING = "STCG_BUDGET_BINDING"
    NO_HOLDINGS_FOR_RECOMMENDED_FUND = "NO_HOLDINGS_FOR_RECOMMENDED_FUND"


class RebalancingWarning(BaseModel):
    code: WarningCode
    message: str
    affected_isins: list[str] = Field(default_factory=list)


class RebalancingTotals(BaseModel):
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


class KnobSnapshot(BaseModel):
    multi_fund_cap_pct: float
    others_fund_cap_pct: float
    rebalance_min_change_pct: float
    exit_floor_rating: int
    ltcg_annual_exemption_inr: Decimal
    stcg_rate_equity_pct: float
    ltcg_rate_equity_pct: float
    st_threshold_months_equity: int
    st_threshold_months_debt: int
    multi_cap_sub_categories: list[str]


class RebalancingRunMetadata(BaseModel):
    computed_at: datetime
    engine_version: str
    request_corpus_inr: Decimal
    knob_snapshot: KnobSnapshot
    request_id: UUID


class TradeAction(BaseModel):
    isin: str
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    action: Literal["BUY", "SELL", "EXIT"]
    amount_inr: Decimal
    reason_code: str                 # machine — stable, analytics
    reason_title: str                # customer card header
    reason_text: str                 # customer card body, one sentence


class SubgroupSummary(BaseModel):
    """Per-asset_subgroup aggregate: target vs current vs final holding,
    plus the action rows for that subgroup. Built by step 6 so the
    presentation layer doesn't have to re-derive these aggregates."""
    asset_subgroup: str
    goal_target_inr: Decimal              # what goal allocation said we want
    current_holding_inr: Decimal          # what's there today (sum of present)
    suggested_final_holding_inr: Decimal  # what we'll have after rebalance
    rebalance_inr: Decimal                # suggested_final − current (signed)
    total_buy_inr: Decimal
    total_sell_inr: Decimal
    ranks_total: int                      # ranks defined for this subgroup
    ranks_with_holding: int               # ranks with present_allocation > 0
    ranks_with_action: int                # ranks with a buy or sell
    actions: list[FundRowAfterStep5] = Field(default_factory=list)


class RebalancingComputeResponse(BaseModel):
    rows: list[FundRowAfterStep5]                             # full audit trail
    subgroups: list[SubgroupSummary] = Field(default_factory=list)  # presentation
    totals: RebalancingTotals
    metadata: RebalancingRunMetadata
    trade_list: list[TradeAction] = Field(default_factory=list)
    warnings: list[RebalancingWarning] = Field(default_factory=list)
