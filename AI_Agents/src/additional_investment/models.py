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
    # Goal-funding status (from the caller). Drives ONLY the legacy single-bucket
    # path (SIP, or lumpsum without a holdings map): the deposit targets the
    # nearest unfunded goal. Defaults exist because the deficit path ignores
    # them (deficit-fill needs no goal flags — the post-investment ideal already
    # encodes goal priority). long_term_fulfilled is intentionally not needed:
    # long-term is always the fallback target.
    short_term_fulfilled: bool = False
    medium_term_fulfilled: bool = False
    # Current holdings value per canonical asset subgroup (scheme_classification
    # vocabulary). When set AND cadence is LUMPSUM, the engine runs DEFICIT FILL:
    # deploy into max(0, ideal_total - current) gaps, proportionally. None (the
    # default) preserves legacy behavior exactly.
    current_value_by_subgroup: Optional[dict[str, float]] = None
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
    # SIP-only (spec 2026-07-05): BUY-trade ISINs of the customer's latest
    # persisted rebalancing run, keyed by asset subgroup, ordered by BUY amount
    # desc. The SIP selector mirrors these funds (equal split per subgroup);
    # a missing subgroup — or None — falls back to that subgroup's rank-1
    # ranked fund. Ignored on the LUMPSUM paths.
    rebal_buy_isins_by_subgroup: Optional[dict[str, list[str]]] = None
    # SIP per-fund cap floor in rupees (amendment 2026-07-06): each SIP buy is
    # capped at max(cap_pct × deploy_amount, this floor). The floor keeps a
    # small monthly amount concentrated in few funds; the percentage keeps a
    # large one from over-concentrating. 0 disables the floor (cap is then
    # just the percentage). Caller sources the value from Rebalancing config
    # (AINV_SIP_FUND_CAP_FLOOR_INR). Ignored on the LUMPSUM paths.
    sip_fund_cap_floor_inr: float = Field(default=0.0, ge=0)
    # Lumpsum per-fund cap floor in rupees (amendment 2026-07-06): same
    # max(cap_pct × deploy_amount, floor) rule for BOTH lumpsum modes
    # (deficit-fill and legacy no-holdings). Caller sources
    # AINV_LUMPSUM_FUND_CAP_FLOOR_INR. Ignored on the SIP path.
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
    undeployed_inr: float = Field(ge=0)  # deploy_amount_inr - deployed_inr (>0 when caps/fund-scarcity bind)
    per_subgroup_target: list[SubgroupTarget]
    buys: list[FundBuy]
