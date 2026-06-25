from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Cadence(str, Enum):
    LUMPSUM = "lumpsum"
    SIP_MONTHLY = "sip_monthly"


class BranchUsed(str, Enum):
    LONG_TERM = "long_term"
    TOTAL_MINUS_EMERGENCY = "total_minus_emergency"


class SubgroupBucketAmounts(BaseModel):
    """Per-subgroup amounts across horizon buckets, lifted from the practical
    allocation output (AggregatedSubgroupRow) on the customer's CURRENT corpus."""

    subgroup: str
    emergency: float = 0.0
    short_term: float = 0.0
    medium_term: float = 0.0
    long_term: float = 0.0
    total: float = 0.0


class RankedFund(BaseModel):
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    recommended_fund: str


class Holding(BaseModel):
    isin: str
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    present_amount_inr: float
    rank: Optional[int] = None       # rank in the ranking list if matched
    rating: Optional[float] = None   # 0..10; >= 5 is acceptable
    force_exit: bool = False         # rank-9999 sentinel — never top up


class AdditionalInvestmentInput(BaseModel):
    deploy_amount_inr: float = Field(gt=0)
    cadence: Cadence
    subgroups: list[SubgroupBucketAmounts]
    medium_term_fulfilled: bool
    ranked_funds: list[RankedFund]
    holdings: list[Holding] = Field(default_factory=list)
    resulting_corpus_inr: float = Field(gt=0)  # existing holdings + deploy, for caps
    cap_pct_by_subgroup: dict[str, float] = Field(default_factory=dict)
    default_cap_pct: float = 10.0
    rounding_multiple_inr: int = 100


class SubgroupTarget(BaseModel):
    subgroup: str
    ratio: float
    target_inr: float


class FundBuy(BaseModel):
    recommended_fund: str
    isin: str
    sub_category: str
    asset_subgroup: str
    amount_inr: float
    monthly_amount_inr: Optional[float] = None  # set when cadence == sip_monthly
    reason: str


class AdditionalInvestmentOutput(BaseModel):
    branch_used: BranchUsed
    cadence: Cadence
    deploy_amount_inr: float
    deployed_inr: float    # sum of buy amounts actually placed
    undeployed_inr: float  # deploy_amount_inr - deployed_inr (>0 when caps/fund-scarcity bind)
    per_subgroup_target: list[SubgroupTarget]
    buys: list[FundBuy]
