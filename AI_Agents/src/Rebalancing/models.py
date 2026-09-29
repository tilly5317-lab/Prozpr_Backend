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

# Cross-agent import: documented exception to peer-isolation per
# `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md` §B.1.
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
    PracticalAllocationInput,
    PracticalAllocationOutput,
)


# ── Per-step row models ──────────────────────────────────────────────────────


class FundRowInput(BaseModel):
    """Engine input row.

    One row per `(asset_subgroup, sub_category, recommended_fund, rank)` slot
    in the rank table. Off-list held funds come in two flavours:

    * Force-exit: `rank = FORCE_EXIT_RANK` (9999), `is_recommended = False`,
      `target_amount_pre_cap = 0`. step2 sets `exit_flag = True`; step4
      fully liquidates regardless of tax.
    * NEUTRAL: `rank = 0`, `is_recommended = False`,
      `target_amount_pre_cap = st_value_inr` (the locked ST minimum).
      step2 sees `diff = -lt_value` (the migratable LT portion), step4's
      optional pool sells from it LT-only — never realising STCG — and
      only when there's recommended-fund buy demand. The ST portion
      stays as-is. The input builder offsets the matching subgroup's
      rank-1 target by `sum(neutral_st_values)` so the engine doesn't
      double-allocate against the stuck ST.
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

    # Holdings-aware floor: the amount step1's per-fund cap must NOT clip below
    # (design note 2026-07-19). Non-zero only for a held fund the rank band
    # protects, and set by the pipeline's target assignment — never by the input
    # builder. Zero means "no protection", which is today's behaviour.
    #
    # Deliberately NOT `present_allocation_inr`: gating the cap on what is held
    # would raise the ceiling for every held row, including one the band
    # declines to protect, letting a customer keep any over-cap position simply
    # by owning it.
    protected_floor_inr: Decimal = Field(default=Decimal(0), ge=0)

    # Present-holding state (zero for not-yet-held funds)
    present_allocation_inr: Decimal = Field(default=Decimal(0), ge=0)
    invested_cost_inr: Decimal = Field(default=Decimal(0), ge=0)

    # Tax-aging breakdown of the present holding
    st_value_inr: Decimal = Field(default=Decimal(0), ge=0)
    st_cost_inr: Decimal = Field(default=Decimal(0), ge=0)
    lt_value_inr: Decimal = Field(default=Decimal(0), ge=0)
    lt_cost_inr: Decimal = Field(default=Decimal(0), ge=0)

    # Current NAV — populated by the bridge for displays; the engine itself
    # does not consume this field. Kept as input so bridges have a stable
    # contract and customer-view formatters can show "as of NAV ₹X".
    current_nav: Decimal = Field(default=Decimal(0), ge=0)

    # Status
    fund_rating: int = Field(default=10, ge=1, le=10)
    is_recommended: bool = True

    # Per-fund rationale (carried from the ranking CSV). Populated for recommended
    # rows (selection_reason) and BAD rows (rejection_reason); the other side is
    # None. Step 6 surfaces the appropriate one on TradeAction.fund_reason.
    selection_reason: Optional[str] = None
    rejection_reason: Optional[str] = None


class FundRowAfterStep1(FundRowInput):
    max_pct: float  # cap that applies to this fund (% of corpus)
    target_pre_cap_pct: float  # original pre-cap target / corpus
    target_own_capped_pct: float  # post-own-cap, before spill received
    final_target_pct: float  # final after spill cascade
    final_target_amount: Decimal  # final rupees, rounded


class FundRowAfterStep2(FundRowAfterStep1):
    diff: Decimal  # final_target_amount − present (signed)
    exit_flag: bool  # forced exit (BAD or low-rated)
    worth_to_change: bool  # |diff| past threshold OR exit_flag
    # Signed target move applied by step2b when a debt-for-debt switch was
    # cancelled. step6 adds it into the per-subgroup `goal_target_inr` so the
    # summary reconciles with `suggested_final_holding_inr`.
    # `target_amount_pre_cap` is deliberately NOT overwritten: its per-subgroup
    # sum equals the practical-allocation output, which ships verbatim on the
    # same response, so mutating it would put two different numbers for one
    # subgroup in a single payload. Zero on every row step2b left alone.
    netted_target_adjustment_inr: Decimal = Decimal(0)


class FundRowAfterStep3(FundRowAfterStep2):
    stcg_amount: Decimal  # st_value − st_cost (signed)
    ltcg_amount: Decimal  # lt_value − lt_cost (signed)


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
    # The four corpus scalars (total / mf / non-MF equity / ELSS) and all
    # profile/goal/market-view fields ride on this nested input. The previous
    # top-level `total_corpus` is now `practical_allocation_input.total_corpus`.
    practical_allocation_input: PracticalAllocationInput
    tax_regime: Literal["old", "new"]
    effective_tax_rate_pct: float = Field(ge=0.0, le=100.0)
    rounding_step: int = Field(default=100, ge=1)

    # Per-request capital-gains state (bucket D). The three fields below are
    # INDEPENDENT inputs — do NOT derive one from another. In particular,
    # stcg_offset_budget_inr must not be populated from the carryforward losses,
    # or the same loss capacity is double-counted (a pass-1 realisation cap AND
    # again as the pass-2 offset).
    #
    #   * stcg_offset_budget_inr — pass-1 cap (step4) on how much STCG may be
    #     realised in this run. Default Decimal(0) = strict brake (no STCG-
    #     incurring sells in pass-1 unless a positive override is set).
    #     None is reserved for INTERNAL counterfactual use (`_sell_from_row`
    #     with stcg_remaining=None disables the cap entirely); production
    #     callers should never pass None.
    #   * carryforward_st_loss_inr — brought-forward SHORT-term capital loss;
    #     offsets realised STCG in the step5 pass-2 top-up.
    #   * carryforward_lt_loss_inr — brought-forward LONG-term capital loss. Per
    #     Indian IT rules LT losses offset only LTCG, never STCG, so this is NOT
    #     used in the STCG offset. LTCG-loss set-off is not yet modeled (LTCG tax
    #     uses the annual exemption only), so this field is currently reserved.
    stcg_offset_budget_inr: Optional[Decimal] = Decimal(0)
    carryforward_st_loss_inr: Decimal = Field(default=Decimal(0), ge=0)
    carryforward_lt_loss_inr: Decimal = Field(default=Decimal(0), ge=0)

    # All MF rows: recommended (rank≥1) and BAD (rank=0). ELSS rows are
    # filtered out by the input builder — ELSS exposure surfaces via
    # `practical_allocation_input.elss_corpus` and as a frozen subgroup row
    # in step6's response.
    rows: list[FundRowInput]

    # Tracing
    request_id: UUID = Field(default_factory=uuid4)

    @property
    def total_corpus(self) -> Decimal:
        """Backwards-compatible accessor; consumers should prefer
        `practical_allocation_input.total_corpus` directly."""
        return Decimal(str(self.practical_allocation_input.total_corpus))


class WarningCode(str, Enum):
    UNREBALANCED_REMAINDER = "UNREBALANCED_REMAINDER"
    BAD_FUND_DETECTED = "BAD_FUND_DETECTED"
    STCG_BUDGET_BINDING = "STCG_BUDGET_BINDING"
    NO_HOLDINGS_FOR_RECOMMENDED_FUND = "NO_HOLDINGS_FOR_RECOMMENDED_FUND"
    DEBT_SWITCH_SUPPRESSED = "DEBT_SWITCH_SUPPRESSED"


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
    unrebalanced_remainder_inr: Decimal
    rows_count: int
    funds_to_buy_count: int
    funds_to_sell_count: int
    funds_to_exit_count: int
    funds_held_count: int


class KnobSnapshot(BaseModel):
    multi_fund_cap_pct: float
    others_fund_cap_pct: float
    # Rupee floor on the per-fund cap (amendment 2026-07-06); default so
    # KnobSnapshot payloads persisted before the field existed still parse.
    fund_cap_floor_inr: Decimal = Decimal("0")
    rebalance_min_change_pct: float
    exit_floor_rating: int
    ltcg_annual_exemption_inr: Decimal
    stcg_rate_equity_pct: float
    ltcg_rate_equity_pct: float
    st_threshold_months_equity: int
    st_threshold_months_debt: int
    multi_fund_cap_subgroups: list[str]
    # Step 2b debt-switch netting (2026-07-18). Defaulted so KnobSnapshot
    # payloads persisted before the field existed still parse — same
    # precedent as `fund_cap_floor_inr` above.
    debt_switch_netting_enabled: bool = True
    debt_netting_subgroups: list[str] = []
    # How step2b redistributes surviving buy demand ("cap_spill" | "pro_rata").
    # Was missing from the 1.2.0 snapshot: two runs with different modes were
    # indistinguishable from their own metadata.
    debt_netting_mode: str = "cap_spill"
    # Holdings-aware targets (2026-07-19). Same defaulting precedent as above.
    holdings_aware_targets_enabled: bool = True
    rank_protect_band: int = 5


class RebalancingRunMetadata(BaseModel):
    computed_at: datetime
    engine_version: str
    request_corpus_inr: Decimal
    knob_snapshot: KnobSnapshot
    request_id: UUID


class TradeAction(BaseModel):
    isin: Optional[str] = None
    asset_subgroup: str
    sub_category: Optional[str] = None
    recommended_fund: Optional[str] = None
    action: Literal["BUY", "SELL", "EXIT", "SELL_DIRECT_STOCKS"]
    amount_inr: Decimal
    reason_code: str  # machine — stable, analytics
    reason_title: str  # customer card header
    reason_text: str  # customer card body, one sentence
    # Per-fund rationale from the ranking CSV. BUY or SELL-trim of a
    # recommended fund → selection_reason; EXIT of a BAD/off-list fund →
    # joined rejection reasons. None only when no fund-specific reason exists
    # (e.g. SELL_DIRECT_STOCKS, which isn't a ranked MF).
    fund_reason: Optional[str] = None


class SubgroupSummary(BaseModel):
    """Per-asset_subgroup aggregate: target vs current vs final holding,
    plus the participating fund rows for that subgroup. Built by step 6
    so the presentation layer doesn't have to re-derive these aggregates.

    `actions` includes every fund row that's part of the plan for this
    subgroup — both rows being traded (buy/sell/exit) and rows being
    held as-is (target unchanged within tolerance, or already at target).
    Phantom rows (zero target and zero holding) are dropped. To filter to
    only traded rows, use the `ranks_with_action` count or check each
    row's pass1_buy_amount / pass1_sell_amount / pass2_sell_amount.

    **Frozen subgroups** (`tax_efficient_equities`, `non_mf_equities`):
    step6 emits these with `actions = []` because they have no MF rows
    in the engine — their amounts come straight from
    `practical_allocation.corpus_breakdown` and no trades are generated
    against them inside the engine (`SELL_DIRECT_STOCKS` rides on
    `trade_list`, not on `SubgroupSummary.actions`)."""

    asset_subgroup: str
    goal_target_inr: Decimal  # what goal allocation said we want
    current_holding_inr: Decimal  # what's there today (sum of present)
    suggested_final_holding_inr: Decimal  # what we'll have after rebalance
    rebalance_inr: Decimal  # suggested_final − current (signed)
    total_buy_inr: Decimal
    total_sell_inr: Decimal
    ranks_total: int  # ranks defined for this subgroup
    ranks_with_holding: int  # ranks with present_allocation > 0
    ranks_with_action: int  # ranks with a buy or sell
    actions: list[FundRowAfterStep5] = Field(default_factory=list)


class RebalancingComputeResponse(BaseModel):
    rows: list[FundRowAfterStep5]  # full audit trail
    subgroups: list[SubgroupSummary] = Field(default_factory=list)  # presentation
    totals: RebalancingTotals
    metadata: RebalancingRunMetadata
    trade_list: list[TradeAction] = Field(default_factory=list)
    warnings: list[RebalancingWarning] = Field(default_factory=list)
    # Verbatim passthrough of the practical allocation output for the
    # ideal-vs-practical UI. Same shape as GoalAllocationOutput + an extras
    # `corpus_breakdown` block surfacing ELSS / non-MF equity numbers.
    practical_allocation: PracticalAllocationOutput
