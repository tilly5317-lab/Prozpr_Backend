from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class Goal(BaseModel):
    goal_name: str
    time_to_goal_months: int = Field(..., ge=1)
    amount_needed: float = Field(..., gt=0)
    goal_priority: Literal["negotiable", "non_negotiable"]
    investment_goal: Literal[
        "wealth_creation", "retirement", "intergenerational_transfer",
        "education", "home_purchase", "other"
    ] = "wealth_creation"


class MultiAssetFundComposition(BaseModel):
    """Internal equity/debt/others breakdown of the multi-asset fund used in long-term allocation."""
    equity_pct: float = Field(..., ge=0, le=100)
    debt_pct: float = Field(..., ge=0, le=100)
    others_pct: float = Field(..., ge=0, le=100)


class MarketCommentaryScores(BaseModel):
    equities: float = Field(default=5.0, ge=1, le=10)
    debt: float = Field(default=5.0, ge=1, le=10)
    others: float = Field(default=5.0, ge=1, le=10)
    low_beta_equities: float = Field(default=5.0, ge=1, le=10)
    value_equities: float = Field(default=5.0, ge=1, le=10)
    dividend_equities: float = Field(default=5.0, ge=1, le=10)
    medium_beta_equities: float = Field(default=5.0, ge=1, le=10)
    high_beta_equities: float = Field(default=5.0, ge=1, le=10)
    sector_equities: float = Field(default=5.0, ge=1, le=10)
    us_equities: float = Field(default=5.0, ge=1, le=10)


class AllocationInput(BaseModel):
    # ── From risk_profiling ──────────────────────────────────────────────────
    effective_risk_score: float = Field(..., ge=1, le=10)
    age: int
    annual_income: float = Field(..., ge=0)
    osi: float = Field(..., ge=0.0, le=1.0)
    savings_rate_adjustment: Literal["none", "equity_boost", "equity_reduce", "skipped"]
    gap_exceeds_3: bool
    shortfall_amount: Optional[float] = None

    # ── Gathered by this module ──────────────────────────────────────────────
    total_corpus: float = Field(..., ge=0)
    monthly_household_expense: float = Field(..., ge=0)
    tax_regime: Literal["old", "new"]
    section_80c_utilized: float = Field(default=0.0, ge=0.0)
    emergency_fund_needed: bool = True
    primary_income_from_portfolio: bool = False
    effective_tax_rate: float = Field(..., ge=0.0, le=100.0)  # percentage 0–100
    goals: List[Goal] = []
    market_commentary: MarketCommentaryScores = Field(default_factory=MarketCommentaryScores)
    multi_asset_composition: MultiAssetFundComposition = Field(
        default_factory=lambda: MultiAssetFundComposition(equity_pct=65.0, debt_pct=25.0, others_pct=10.0)
    )

    # ── From risk_profiling internals (optional) ─────────────────────────────
    risk_willingness: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    net_financial_assets: Optional[float] = None
    occupation_type: Optional[str] = None


# ── Output models ─────────────────────────────────────────────────────────────

class BucketShortfall(BaseModel):
    bucket: Optional[Literal["emergency", "short_term", "medium_term", "long_term"]] = None
    shortfall_amount: float = Field(default=0.0, ge=0)
    message: Optional[str] = None


class SubgroupFundMapping(BaseModel):
    asset_class: Literal["equity", "debt", "others"]

    @field_validator("asset_class", mode="before")
    @classmethod
    def _normalise_asset_class(cls, v: Any) -> Any:
        if isinstance(v, str):
            mapping = {"equities": "equity", "debts": "debt"}
            return mapping.get(v.lower(), v)
        return v

    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    amount: float = Field(..., ge=0)


class BucketAllocation(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    goals: List[Union[Goal, str]]
    total_goal_amount: float = Field(..., ge=0)
    allocated_amount: float = Field(..., ge=0)
    shortfall: Optional[BucketShortfall] = None
    subgroup_amounts: dict  # subgroup -> amount


class AggregatedSubgroupRow(BaseModel):
    subgroup: str
    sub_category: str
    emergency: float = Field(..., ge=0)
    short_term: float = Field(..., ge=0)
    medium_term: float = Field(..., ge=0)
    long_term: float = Field(..., ge=0)
    total: float = Field(..., ge=0)
    fund_mapping: Optional[SubgroupFundMapping] = None


class ClientSummary(BaseModel):
    age: int
    occupation: Optional[str] = None
    effective_risk_score: float
    total_corpus: float
    goals: List[Goal]


def _normalise_shortfall(v: Any) -> Any:
    """Convert any LLM shortfall shape into a BucketShortfall-compatible dict or None."""
    if v is None or v is False:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return {"shortfall_amount": float(v)} if v > 0 else None
    if isinstance(v, dict):
        if "shortfall_amount" in v:
            return v
        # {'flag': True/False, ...}
        if "flag" in v:
            amount = float(v.get("shortfall_amount", 0.0))
            return {"shortfall_amount": amount, "bucket": v.get("bucket"), "message": v.get("message")} if amount > 0 else None
        # Any dict — find first positive numeric value
        for val in v.values():
            if isinstance(val, (int, float)) and val > 0:
                return {"shortfall_amount": float(val), "bucket": v.get("bucket"), "message": v.get("message")}
        return None
    return v


class GoalAllocationOutput(BaseModel):
    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    shortfall_summary: List[BucketShortfall]
    grand_total: float
    all_amounts_in_multiples_of_100: bool

    @model_validator(mode="before")
    @classmethod
    def _normalise_llm_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for bucket in data.get("bucket_allocations", []):
            if isinstance(bucket, dict):
                bucket["shortfall"] = _normalise_shortfall(bucket.get("shortfall"))
        return data
