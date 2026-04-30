from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ShortTermExpense(BaseModel):
    amount: float
    timeline_in_months: int


class AllocationInput(BaseModel):
    # ── From risk_profiling (passed in) ──────────────────────────────────────
    effective_risk_score: float = Field(..., ge=1, le=10)
    age: int
    annual_income: float
    osi: float = Field(..., ge=0.0, le=1.0)
    savings_rate_adjustment: Literal["none", "equity_boost", "equity_reduce", "skipped"]
    gap_exceeds_3: bool
    shortfall_amount: Optional[float] = None  # absolute shortfall if NFA < 0, else None

    # ── Gathered by this module ───────────────────────────────────────────────
    total_corpus: float
    monthly_household_expense: float
    investment_horizon: str  # e.g. "long", "medium", "short", or "7 years"
    investment_horizon_years: Optional[float] = None  # required if medium-term
    investment_goal: str  # e.g. "wealth creation", "regular income", "intergenerational transfer"
    tax_regime: Literal["old", "new"]
    section_80c_utilized: float = Field(default=0.0, ge=0.0)
    emergency_fund_needed: bool = True
    primary_income_from_portfolio: bool = False
    short_term_expenses: List[ShortTermExpense] = []

    # ── Derived from risk_profiling internals (optional, used for guardrails) ─
    risk_willingness: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    savings_rate: Optional[float] = None  # raw savings rate (income-expense)/income
    net_financial_assets: Optional[float] = None  # for NFA carve-out
    occupation_type: Optional[str] = None


# ── Output models (step5_presentation) ───────────────────────────────────────

class CarveOut(BaseModel):
    type: str
    amount: float
    fund_type: str
    asset_subgroup: str


class AssetClassAmount(BaseModel):
    pct: int
    amount: float


class AssetClassAllocation(BaseModel):
    equities: AssetClassAmount
    debt: AssetClassAmount
    others: AssetClassAmount


class SubgroupItem(BaseModel):
    subgroup: str
    asset_class: str
    recommended_fund: str
    asset_class_subcategory: str
    isin: str
    pct: int
    amount: float


class SubgroupAllocation(BaseModel):
    equity: List[SubgroupItem]
    debt: List[SubgroupItem]
    others: List[SubgroupItem]


class ClientSummary(BaseModel):
    age: int
    occupation: Optional[str] = None
    investment_horizon: str
    investment_goal: str
    effective_risk_score: float
    total_corpus: float


class AllocationOutput(BaseModel):
    client_summary: ClientSummary
    carve_outs: List[CarveOut]
    total_carve_outs: float
    remaining_investable_corpus: float
    asset_class_allocation: AssetClassAllocation
    subgroup_allocation: SubgroupAllocation
    all_amounts_in_multiples_of_100: bool
    grand_total: float
