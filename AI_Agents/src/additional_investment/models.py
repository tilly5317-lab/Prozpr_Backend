"""Pydantic I/O models for the additional-investment engine.

Money amounts are plain `float` (rupees), deliberately matching the allocation
family this engine composes with (asset_allocation_pydantic / practical_asset_allocation),
not the `Decimal` used by Rebalancing — there is no tax-lot arithmetic here and
buys are rounded down to ₹100 multiples, so float precision is bounded.
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
    """Horizon bucket the deposit is deployed toward (the nearest unfunded goal)."""

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
    # Goal-funding status (from the caller). The deposit targets the nearest unfunded
    # goal: short-term if unfulfilled, else medium-term if unfulfilled, else long-term
    # (which also catches the all-funded case — keep building long-term). long_term_fulfilled
    # is intentionally not needed: long-term is always the fallback target.
    short_term_fulfilled: bool
    medium_term_fulfilled: bool
    ranked_funds: list[RankedFund]
    # Per-fund concentration cap, as a percent of the DEPLOY amount (this SIP/lumpsum),
    # keyed by subgroup (e.g. debt 30, multi_asset 20, others 10). A subgroup's share
    # spreads across its top funds so no single fund exceeds its cap of the deposit.
    cap_pct_by_subgroup: dict[str, float] = Field(default_factory=dict)
    default_cap_pct: float = 10.0
    rounding_multiple_inr: int = 100
    # Subgroups ineligible for fresh deployment (caller policy). Excluded from the
    # split entirely, so their share renormalises onto the remaining subgroups —
    # e.g. non_mf_equities (direct stocks, no funds) and tax_efficient_equities (ELSS lock-in).
    exclude_subgroups: set[str] = Field(default_factory=set)


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
    undeployed_inr: float = Field(ge=0)  # deploy_amount_inr - deployed_inr (>0 when caps/fund-scarcity bind)
    per_subgroup_target: list[SubgroupTarget]
    buys: list[FundBuy]
