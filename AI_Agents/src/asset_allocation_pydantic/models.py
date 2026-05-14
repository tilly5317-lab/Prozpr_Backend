from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .tables import (
    DEFAULT_MARKET_COMMENTARY_SCORES,
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
)


# ── Inputs ────────────────────────────────────────────────────────────────────


InvestmentGoal = str


class Goal(BaseModel):
    goal_name: str
    time_to_goal_months: int = Field(..., ge=1)
    amount_needed: float = Field(..., gt=0)
    goal_priority: Literal["negotiable", "non_negotiable"]
    investment_goal: InvestmentGoal = "wealth_creation"


class MultiAssetFundComposition(BaseModel):
    equity_pct: float = Field(..., ge=0, le=100)
    debt_pct: float = Field(..., ge=0, le=100)
    others_pct: float = Field(..., ge=0, le=100)


_AC = DEFAULT_MARKET_COMMENTARY_SCORES["asset_class"]
_SG = DEFAULT_MARKET_COMMENTARY_SCORES["subgroup"]


class MarketCommentaryScores(BaseModel):
    equities: float = Field(default=_AC["equities"], ge=1, le=10)
    debt: float = Field(default=_AC["debt"], ge=1, le=10)
    others: float = Field(default=_AC["others"], ge=1, le=10)
    low_beta_equities: float = Field(default=_SG["low_beta_equities"], ge=1, le=10)
    value_equities: float = Field(default=_SG["value_equities"], ge=1, le=10)
    dividend_equities: float = Field(default=_SG["dividend_equities"], ge=1, le=10)
    medium_beta_equities: float = Field(default=_SG["medium_beta_equities"], ge=1, le=10)
    high_beta_equities: float = Field(default=_SG["high_beta_equities"], ge=1, le=10)
    sector_equities: float = Field(default=_SG["sector_equities"], ge=1, le=10)
    us_equities: float = Field(default=_SG["us_equities"], ge=1, le=10)


class AllocationInput(BaseModel):
    effective_risk_score: float = Field(..., ge=1, le=10)
    age: int
    annual_income: float = Field(..., ge=0)
    osi: float = Field(..., ge=0.0, le=1.0)
    savings_rate_adjustment: Literal["none", "equity_boost", "equity_reduce", "skipped"]
    gap_exceeds_3: bool
    shortfall_amount: Optional[float] = None

    total_corpus: float = Field(..., ge=0)
    monthly_household_expense: float = Field(..., ge=0)
    tax_regime: Literal["old", "new"]
    section_80c_utilized: float = Field(default=0.0, ge=0.0)
    emergency_fund_needed: bool = True
    primary_income_from_portfolio: bool = False
    intergenerational_transfer: bool = False
    effective_tax_rate: float = Field(..., ge=0.0, le=100.0)
    goals: List[Goal] = []
    market_commentary: MarketCommentaryScores = Field(default_factory=MarketCommentaryScores)
    multi_asset_composition: MultiAssetFundComposition = Field(
        default_factory=lambda: MultiAssetFundComposition(
            equity_pct=DEFAULT_MULTI_ASSET_COMPOSITION_PCTS[0],
            debt_pct=DEFAULT_MULTI_ASSET_COMPOSITION_PCTS[1],
            others_pct=DEFAULT_MULTI_ASSET_COMPOSITION_PCTS[2],
        )
    )

    risk_willingness: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    net_financial_assets: Optional[float] = None
    occupation_type: Optional[str] = None


# ── Shared output primitives ──────────────────────────────────────────────────


class FutureInvestment(BaseModel):
    bucket: Optional[Literal["emergency", "short_term", "medium_term", "long_term"]] = None
    future_investment_amount: float = Field(default=0.0, ge=0)
    message: Optional[str] = None


class BucketAllocation(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    goals: List[Goal]
    total_goal_amount: float = Field(..., ge=0)
    allocated_amount: float = Field(..., ge=0)
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]
    rationale: Optional[str] = None
    goal_rationales: dict[str, str] = Field(default_factory=dict)


class AggregatedSubgroupRow(BaseModel):
    subgroup: str
    emergency: float = Field(..., ge=0)
    short_term: float = Field(..., ge=0)
    medium_term: float = Field(..., ge=0)
    long_term: float = Field(..., ge=0)
    total: float = Field(..., ge=0)


class ClientSummary(BaseModel):
    age: int
    occupation: Optional[str] = None
    effective_risk_score: float
    total_corpus: float
    goals: List[Goal]


class BucketAssetClassSplit(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    equity: int = Field(..., ge=0)
    debt: int = Field(..., ge=0)
    others: int = Field(..., ge=0)
    equity_pct: float = Field(default=0.0, ge=0, le=100)
    debt_pct: float = Field(default=0.0, ge=0, le=100)
    others_pct: float = Field(default=0.0, ge=0, le=100)


class AssetClassSplitBlock(BaseModel):
    per_bucket: List[BucketAssetClassSplit]
    equity_total: int = Field(..., ge=0)
    debt_total: int = Field(..., ge=0)
    others_total: int = Field(..., ge=0)
    equity_total_pct: float = Field(default=0.0, ge=0, le=100)
    debt_total_pct: float = Field(default=0.0, ge=0, le=100)
    others_total_pct: float = Field(default=0.0, ge=0, le=100)


class SubgroupBucketAllocation(BaseModel):
    subgroup: str
    amount: int = Field(..., ge=0)
    pct_of_bucket: float = Field(default=0.0, ge=0, le=100)


class SubgroupBucketSplit(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    subgroups: List[SubgroupBucketAllocation]


class SubgroupBreakdown(BaseModel):
    planned: List[SubgroupBucketSplit]
    actual: List[SubgroupBucketSplit]


class AssetClassBreakdown(BaseModel):
    planned: AssetClassSplitBlock
    actual: AssetClassSplitBlock
    actual_sum_matches_grand_total: bool
    subgroups: Optional[SubgroupBreakdown] = None


class GoalAllocationOutput(BaseModel):
    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    future_investments_summary: List[FutureInvestment]
    grand_total: float
    all_amounts_in_multiples_of_100: bool
    asset_class_breakdown: AssetClassBreakdown


# ── Per-step output models ────────────────────────────────────────────────────


class Step1Output(BaseModel):
    emergency_fund_months: int
    emergency_fund_amount: int
    nfa_carveout_amount: int
    total_emergency: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]


class Step2Output(BaseModel):
    goals_allocated: List[Goal]
    asset_subgroup: Literal["debt_subgroup", "arbitrage"]
    total_goal_amount: int
    allocated_amount: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]


class MediumTermGoalAllocation(BaseModel):
    goal_name: str
    time_to_goal_months: int
    amount_needed: float
    goal_priority: str
    horizon_years: int
    equity_pct: int
    debt_pct: int
    equity_amount: int
    debt_amount: int


class Step3Output(BaseModel):
    risk_bucket: Literal["Low", "Medium", "High"]
    asset_subgroup: Literal["arbitrage_plus_income", "debt_subgroup"]
    goals_allocated: List[MediumTermGoalAllocation]
    total_goal_amount: int
    allocated_amount: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]


class AssetClassAllocation(BaseModel):
    equities_pct: int
    debt_pct: int
    others_pct: int
    equities_amount: int
    debt_amount: int
    others_amount: int


class ElssBlock(BaseModel):
    applicable: bool
    elss_headroom: Optional[int] = None
    elss_amount: int
    residual_equity_corpus: int


class MultiAssetBlock(BaseModel):
    multi_asset_amount: int
    equity_component: int
    debt_component: int
    others_component: int
    equity_for_subgroups: int
    debt_for_subgroups: int
    remaining_others_for_gold: int


class Step4Output(BaseModel):
    asset_class_allocation: AssetClassAllocation
    planned_asset_class_allocation: Optional[AssetClassAllocation] = None
    planned_subgroup_amounts: Optional[dict[str, int]] = None
    elss: ElssBlock
    multi_asset: MultiAssetBlock
    goals_allocated: List[Goal]
    leftover_corpus: int
    total_long_term_corpus: int
    total_allocated: int
    remaining_corpus: int = 0
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]


class AggregatedRow(BaseModel):
    subgroup: str
    emergency: int
    short_term: int
    medium_term: int
    long_term: int
    total: int


class Step5Output(BaseModel):
    rows: List[AggregatedRow]
    grand_total: int
    grand_total_matches_corpus: bool


class ValidationBlock(BaseModel):
    all_rules_pass: bool
    violations_found: List[str] = []
    adjustments_made: List[str] = []


class Step6Output(BaseModel):
    validation: ValidationBlock
