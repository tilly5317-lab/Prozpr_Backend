"""Engine-private intermediate types. NOT exported from goal_planning.__init__."""
from __future__ import annotations
from datetime import date
from typing import Any
from pydantic import BaseModel
from goal_planning.models import (
    GoalType, MortgageAmortizationRow, RetirementSnapshot,
    GoalFundingStatus, OneOffFundingStatus, MonthlyNFARow,
)


class RunContext(BaseModel):
    # Profile (resolved)
    nfa: float
    latest_update_date: date
    annual_income: float
    annual_household_expense: float
    monthly_household_expense: float
    monthly_investment_next_12m: float | None
    tax_rate: float

    # Date anchors
    current_fy_end: date
    current_fy_year: int
    near_term_end: date
    medium_term_end: date
    horizon_cap_years: int = 80

    # Resolved retirement (populated by .with_retirement())
    retirement_date_considered: date | None = None
    retired_portfolio_roi_annual: float
    real_roi_retired_monthly: float

    # Assumption snapshot
    sip_share: float
    annual_income_growth: float
    annual_invested_amount_growth: float
    inflation_household_expense: float
    near_term_roi: float
    mid_term_roi: float
    long_term_roi: float

    def with_retirement(self, snap: RetirementSnapshot) -> "RunContext":
        return self.model_copy(update={
            "retirement_date_considered": snap.retirement_date,
            "retired_portfolio_roi_annual": snap.real_roi_annual,
            "real_roi_retired_monthly": snap.real_roi_monthly,
        })


class MortgageAnnualRow(BaseModel):
    fy_end: date
    opening_balance: float
    annual_interest: float
    annual_principal: float
    annual_emi_total: float
    closing_balance: float


class MortgageSchedule(BaseModel):
    property_ref: str
    start_date: date
    monthly_rows: list[MortgageAmortizationRow]
    annual_rows: list[MortgageAnnualRow]

    def total_emi_in_fy(self, fy_end: date) -> float:
        for row in self.annual_rows:
            if row.fy_end == fy_end:
                return row.annual_emi_total
        return 0.0

    def total_emi_in_month(self, month_end: date) -> float:
        for row in self.monthly_rows:
            if row.month_end == month_end:
                return row.emi
        return 0.0


class GoalPropertyOutcome(BaseModel):
    name: str
    target_fv: float
    payout_amount_fv: float
    mortgage_amount: float
    amortization: MortgageSchedule | None
    goal_date: date
    amount_pv: float


class GoalInternal(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    goal_date_fy: date
    amount_pv: float
    amount_fv: float
    inflation_rate: float
    expected_roi: float
    fund_today_pv: float


class FundingResult(BaseModel):
    nfa_monthly: list[MonthlyNFARow]
    closing_nfa: float
    min_nfa_in_horizon: float
    per_goal_status: list[GoalFundingStatus]
    per_one_off_outflow_status: list[OneOffFundingStatus]
    per_outflow_underfunded_total: dict[str, float]
    per_outflow_funded_amount: dict[str, float]
