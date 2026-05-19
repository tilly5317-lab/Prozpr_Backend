"""Engine-private intermediate types. NOT exported from cashflow_statement.__init__."""
from __future__ import annotations
from datetime import date
from pydantic import BaseModel
from cashflow_statement.models import (
    GoalType, RetirementSnapshot,
    GoalFundingStatus, OneOffFundingStatus, MonthlyCashflowRow,
)


class RunContext(BaseModel):
    # Profile (resolved)
    corpus: float
    latest_update_date: date
    annual_income: float
    annual_household_expense: float
    monthly_household_expense: float
    starting_monthly_investment: float | None
    effective_tax_rate: float

    # Date anchors
    current_fy_end: date
    current_fy_year: int
    near_term_end: date
    medium_term_end: date
    horizon_cap_years: int = 80

    # Resolved retirement (populated by .with_retirement())
    retirement_date_considered: date | None = None
    retired_portfolio_roi_annual: float  # nominal; consumed by retirement.py pre-`with_retirement`

    # Assumption snapshot
    sip_share: float
    annual_income_growth: float
    annual_invested_amount_growth: float
    inflation_household_expense: float
    inflation_property: float
    near_term_roi: float
    mid_term_roi: float
    long_term_roi: float
    default_mortgage_tenure_years: int
    default_mortgage_interest_annual: float

    def with_retirement(self, snap: RetirementSnapshot) -> "RunContext":
        # Items #7/#7a: only update retirement_date_considered. The previous overwrites of
        # retired_portfolio_roi_annual and real_roi_retired_monthly were unused downstream
        # and silently changed nominal→real semantics for the former.
        return self.model_copy(update={
            "retirement_date_considered": snap.retirement_date,
        })


class MortgageSchedule(BaseModel):
    """Engine-internal mortgage amortization summary.

    Per-month interest/principal/balance breakdown is intentionally NOT tracked here —
    that detail belongs in a standalone amortization tool, not the cashflow projection.
    The cashflow projection only needs FY-level EMI totals and the date the mortgage closes.
    """
    property_ref: str
    start_date: date
    end_date: date | None  # last EMI month_end; None if mortgage doesn't close within horizon
    annual_emi_by_fy: dict[date, float]  # fy_end -> sum of EMIs paid in that FY

    def total_emi_in_fy(self, fy_end: date) -> float:
        return self.annual_emi_by_fy.get(fy_end, 0.0)


class GoalPropertyOutcome(BaseModel):
    name: str
    target_fv: float
    corpus_required_fv: float
    mortgage_amount: float
    amortization: MortgageSchedule | None
    goal_date: date
    goal_value_pv: float
    inflation_used: float   # the rate actually applied (user override or assumption)


class GoalInternal(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    goal_date_fy: date
    goal_value_pv: float        # full goal value in today's ₹
    goal_value_fv: float        # full goal value at goal_date (inflated)
    corpus_required_fv: float   # corpus drain at goal_date (=goal_value_fv unless mortgaged property)
    inflation_rate: float
    expected_roi: float
    investment_required_pv: float


class FundingResult(BaseModel):
    monthly_enriched: list[MonthlyCashflowRow]  # cashflow rows with corpus fields filled in
    corpus_closing: float
    per_goal_status: list[GoalFundingStatus]
    per_one_off_outflow_status: list[OneOffFundingStatus]
