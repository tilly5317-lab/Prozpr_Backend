"""Pydantic I/O models for the additional-investment engine.

Money amounts are plain `float` (rupees), deliberately matching the allocation
family this engine composes with (asset_allocation_pydantic / practical_asset_allocation),
not the `Decimal` used by Rebalancing — there is no tax-lot arithmetic here and
buys are rounded to the NEAREST ₹100 multiple (halves up — see
selection.py:_round_to_multiple), so float precision is bounded.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──

class Cadence(str, Enum):
    LUMPSUM = "lumpsum"
    SIP_MONTHLY = "sip_monthly"


class TargetBucket(str, Enum):
    """Horizon that receives most of the deposit — a label, not the split driver."""

    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


# ── Input models ──

class SubgroupBucketAmounts(BaseModel):
    """Per-subgroup amounts across horizon buckets, lifted from the practical
    allocation output (AggregatedSubgroupRow) on the customer's CURRENT corpus."""

    subgroup: str
    emergency: float = Field(default=0.0, ge=0)
    short_term: float = Field(default=0.0, ge=0)
    medium_term: float = Field(default=0.0, ge=0)
    long_term: float = Field(default=0.0, ge=0)
    total: float = Field(default=0.0, ge=0)


class RankedFund(BaseModel):
    """One ranked fund candidate for a subgroup (rank 1 = most preferred)."""

    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    recommended_fund: str


class AdditionalInvestmentInput(BaseModel):
    """Engine input: how much to deploy plus the allocation / goal context.

    Holding-agnostic: recommendations come purely from `ranked_funds`; the
    customer's existing holdings are deliberately not an input here.
    """

    deploy_amount_inr: float = Field(gt=0)
    cadence: Cadence
    subgroups: list[SubgroupBucketAmounts]
    # Investable corpus at deploy time (= total_corpus − non_mf_equity), pre-computed
    # by the caller; decides 1 vs 2 funds per subgroup (spec 2026-09-24). 0 → N=1.
    investable_corpus_inr: float = Field(default=0.0, ge=0)
    # Money for short-term goals, deployed first into goal_subgroup by name; the
    # rest follows the long-term plan (SIP) or the long-term deficits (lumpsum).
    goal_share_inr: float = Field(default=0.0, ge=0)
    goal_subgroup: Optional[str] = None
    # Current holdings value per canonical asset subgroup (scheme_classification
    # vocabulary). When set AND cadence is LUMPSUM, the engine runs DEFICIT FILL:
    # deploy into max(0, ideal_total - current) gaps, proportionally. None (the
    # default) preserves legacy behavior exactly.
    current_value_by_subgroup: Optional[dict[str, float]] = None
    ranked_funds: list[RankedFund]
    # VESTIGIAL (spec 2026-09-24): per-fund caps no longer bound selection — top-1/2
    # by corpus replaced them. Retained, still populated by the builder, ignored here.
    cap_pct_by_subgroup: dict[str, float] = Field(default_factory=dict)
    default_cap_pct: float = 10.0
    rounding_multiple_inr: int = 100
    # Subgroups ineligible for fresh deployment (caller policy). Excluded from the
    # split entirely, so their share renormalises onto the remaining subgroups —
    # e.g. non_mf_equities (direct stocks, no funds) and tax_efficient_equities (ELSS lock-in).
    exclude_subgroups: set[str] = Field(default_factory=set)
    # VESTIGIAL (spec 2026-09-24): the SIP-mirrors-rebalancing selector is retired,
    # so nothing sets this any more. Retained only because it round-trips through the
    # persisted request_input JSON (no typed column) — cleanly removable in a later pass.
    rebal_buy_isins_by_subgroup: Optional[dict[str, list[str]]] = None
    # VESTIGIAL (spec 2026-09-24): per-fund cap floors no longer apply — top-1/2 by
    # corpus replaced them. Retained, still populated by the builder from Rebalancing
    # config (AINV_{SIP,LUMPSUM}_FUND_CAP_FLOOR_INR), ignored here.
    sip_fund_cap_floor_inr: float = Field(default=0.0, ge=0)
    lumpsum_fund_cap_floor_inr: float = Field(default=0.0, ge=0)


# ── Output models ──

class SubgroupTarget(BaseModel):
    """Per-subgroup deploy target: its renormalised ratio and rupee amount."""

    subgroup: str
    ratio: float = Field(ge=0)
    target_inr: float = Field(ge=0)


class FundBuy(BaseModel):
    """One BUY instruction emitted by the engine."""

    recommended_fund: str
    isin: str
    sub_category: str
    asset_subgroup: str
    amount_inr: float = Field(ge=0)
    monthly_amount_inr: Optional[float] = None  # set when cadence == sip_monthly
    reason: str


class AdditionalInvestmentOutput(BaseModel):
    """Engine output: the BUY list, per-subgroup targets, and deploy accounting."""

    target_bucket: TargetBucket
    cadence: Cadence
    deploy_amount_inr: float = Field(ge=0)
    deployed_inr: float = Field(ge=0)    # sum of buy amounts actually placed
    undeployed_inr: float = Field(ge=0)  # deploy_amount_inr - deployed_inr (>0 when fund scarcity binds)
    per_subgroup_target: list[SubgroupTarget]
    buys: list[FundBuy]
