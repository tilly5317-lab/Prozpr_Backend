# Pydantic + SQLAlchemy models

from __future__ import annotations
import datetime
from typing import List, Dict, Optional, Literal, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker


# =========================
# Pydantic models (IPS spec)
# =========================

# Purpose: Captures the client's personal/professional context.
class ClientBackground(BaseModel):
    client_name: str = Field(description="name of the person for whom the Investment Policy Statement is prepared")
    age: Optional[int] = Field(default=None, description="age of the person or can be computed from the date of birth")
    occupation: Optional[str] = Field(default=None, description="occupation of the person")
    family_details: Optional[str] = Field(default=None, description="brief description of the family situation, size of family, number of dependents and other earning members")
    wealth_source: Optional[str] = Field(default=None, description="salary income, business earnings or from one off windfall gains like sale of business, lottery gains, gifts, succession etc")
    core_values: Optional[str] = Field(default=None, description="any preferred areas to invest such as philanthropy, ESG or any prohibitive areas")

# Purpose: Represents a single financial goal. A client can have multiple goals (stored as List[Goal])
class Goal(BaseModel):
    description: str
    target_year: int
    goal_type: Literal["growth", "income", "retirement", "expense"]
    amount: Optional[float] = None          # Amount in today's money
    inflation_rate: Optional[float] = None  # Annual inflation, decimal (e.g. 0.07)

# Purpose: Defines what the client wants from their investments (capital appreciation vs. regular income).
class ReturnObjective(BaseModel):
    primary_objectives: Literal["growth", "income", "retirement", "expense"]
    description: Optional[str] = Field(default=None)
    required_rate_of_return: Optional[float] = Field(default=None)
    income_requirement: Optional[float] = Field(default=None)
    currency: Optional[str] = None

#Purpose: Captures both psychological comfort and financial capacity for risk
class RiskTolerance(BaseModel):
    overall_risk_tolerance: Optional[Literal["low", "below_average", "average", "above_average", "high"]] = None
    ability_to_take_risk: Optional[Literal["low", "below_average", "average", "above_average", "high"]] = None
    willingness_to_take_risk: Optional[Literal["low", "below_average", "average", "above_average", "high"]] = None
    ability_drivers: Optional[str] = None
    willingness_drivers: Optional[str] = None

#Purpose: Cash flow planning and liquidity management.
class FinancialNeeds(BaseModel):
    investible_assets: Optional[float] = None
    liabilities: Optional[float] = None
    properties: Optional[float] = None
    mortgage: Optional[float] = None
    expected_inflows: Optional[str] = None
    regular_outflows: Optional[str] = None
    planned_large_outflows: Optional[str] = None
    emergency_fund_requirement: Optional[float] = None
    liquidity_timeframe: Optional[str] = None

#Purpose: Target portfolio allocation (should sum to 100%)
class StrategicAssetAllocation(BaseModel):
    equities: Optional[float] = None
    largecap_equities: Optional[float] = None
    midcap_equities: Optional[float] = None
    smallcap_equities: Optional[float] = None
    flexicap_equities: Optional[float] = None
    global_equities: Optional[float] = None
    pms: Optional[float] = None
    fixed_income: Optional[float] = None
    longduration_fixedincome: Optional[float] = None
    shortduration_fixedincome: Optional[float] = None
    alternatives: Optional[float] = None
    gold_etf: Optional[float] = None
    cash: Optional[float] = None
    other_assets: Optional[Dict[str, float]] = None
    # NEW: Optionally, store guardrails for audit
    min_max: Optional[Dict[str, Dict[str, float]]] = None  # e.g. {"equities": {"min": 10, "max": 80}, ...}

#Purpose: Investment rules/constraints (compliance, ethics, risk limits).
class InvestmentGuidelines(BaseModel):
    permissible_investments: Optional[List[str]] = None
    prohibited_investments: Optional[List[str]] = None
    diversification_guidelines: Optional[str] = None
    leverage_policy: Optional[str] = None
    derivatives_policy: Optional[str] = None

#Purpose: Investment timeline affects asset allocation (longer = more aggressive).
class TimeHorizon(BaseModel):
    is_multi_stage: bool = False
    total_horizon_years: Optional[float] = None
    stages_description: Optional[str] = None

#Purpose: Tax optimization (tax-loss harvesting, municipal bonds, etc.).
class TaxProfile(BaseModel):
    current_incometax_rate: Optional[float] = None
    current_capitalgainstax_rate: Optional[float] = None
    tax_notes: Optional[str] = None

#Purpose: Ongoing portfolio monitoring schedule.
class ReviewProcess(BaseModel):
    meeting_frequency: Optional[Literal["monthly", "quarterly", "semi_annual"]] = None
    review_triggers: Optional[str] = None
    update_process: Optional[str] = None

#Purpose: Complete client profile combining: Qualitative data (goals, risk tolerance), Quantitative data (assets, liabilities, cash flows)
class ClientSnapshot(BaseModel):
    background: ClientBackground
    goals: List[Goal]
    return_objective: ReturnObjective
    risk_tolerance: RiskTolerance
    financial_needs: FinancialNeeds
    tax_profile: TaxProfile
    tax_rate: Optional[float] = None
    time_horizon: TimeHorizon
    review_process: ReviewProcess
    strategic_asset_allocation: Optional[StrategicAssetAllocation] = None
    profile_summary: Optional[str] = None
    risk_return_assessment: Optional[str] = None
    goals_alignment_assessment: Optional[str] = None
    existing_positions_raw: Optional[str] = None  # raw text / JSON from statement
    asset_allocation_rationale: Optional[str] = None
    # Optionally normalized view:
    existing_positions: Optional[Dict[str, float]] = None  # symbol -> market_value

    # numeric & cash-flow fields
    annual_income: Optional[float] = None
    annual_expenses: Optional[float] = None
    one_off_future_expenses: List[Tuple[int, float, str]] = []
    one_off_future_inflows: List[Tuple[int, float, str]] = []

    total_mutual_funds: Optional[float] = None
    total_equities: Optional[float] = None
    total_debt: Optional[float] = None
    total_cash_bank: Optional[float] = None
    total_liabilities: Optional[float] = None
    properties_value: Optional[float] = None
    
    # Mortgage details for cash flow and net worth calculations
    mortgage_balance: Optional[float] = None
    mortgage_interest_rate: Optional[float] = None  # annual, decimal
    mortgage_emi: Optional[float] = None

    # For projection purposes- can be inputted or estimated from historical data 
    current_fy: Optional[int] = None
    income_growth_rate: Optional[float] = None
    expense_growth_rate: Optional[float] = None
    roi_rate: Optional[float] = None


# =========================
# SQLite
# =========================

# Design Pattern: Hybrid Storage Strategy
# ┌─────────────────────────────────────────────────┐
# │  Structured Columns (for queries/filtering)     │  ← Fast lookups
# │  JSON Blob (for complete data)                  │  ← Full fidelity
# └─────────────────────────────────────────────────┘

Base = declarative_base()
class ClientRecord(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

  # Indexed/searchable fields (denormalized for performance)
    client_name = Column(String, index=True)
    occupation = Column(String)
    primary_objective = Column(String)
    overall_risk = Column(String)
    currency = Column(String)

   # Full snapshot stored as JSON
    payload_json = Column(Text)


engine = create_engine("sqlite:///wealth_agent.db")     # Local SQLite file
Base.metadata.create_all(engine)                        # Create tables
SessionLocal = sessionmaker(bind=engine)                # Session factory
