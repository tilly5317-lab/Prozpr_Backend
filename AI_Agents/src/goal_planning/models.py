"""Public Pydantic contracts for the goal_planning module.

All types here cross the engine↔agent boundary or are part of the public API
exported from goal_planning/__init__.py.
"""
from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class Assumptions(BaseModel):
    inflation_property: float = 0.06
    inflation_child_abroad_education: float = 0.08
    inflation_child_local_education: float = 0.06
    inflation_child_marriage: float = 0.06
    inflation_household_expense: float = 0.06
    annual_income_growth: float = 0.08
    annual_invested_amount_growth: float = 0.08
    roi_near_term_post_tax: float = 0.05
    roi_mid_term_post_tax: float = 0.07
    roi_long_term_post_tax: float = 0.09
    roi_retired_portfolio_annual: float = 0.09
    near_term_horizon_years: int = 2
    medium_term_horizon_years: int = 3
    default_mortgage_tenure_years: int = 30
    default_mortgage_interest_annual: float = 0.075


class ClientProfile(BaseModel):
    latest_update_date: date
    annual_income: float
    tax_rate: float
    financial_assets: float
    financial_liabilities_excl_mortgage: float
    monthly_household_expense: float
    monthly_investment_next_12m: float | None = None


class RetirementInput(BaseModel):
    date_of_birth: date
    retirement_age: int = 60
    assumed_total_age: int = 85
    retirement_date_override: date | None = None
    retirement_corpus_pv_override: float | None = None


class CurrentProperty(BaseModel):
    name: str
    has_mortgage: bool
    mortgage_balance: float | None = None
    mortgage_emi: float | None = None
    mortgage_last_date: date | None = None
    mortgage_balance_as_of_date: date | None = None


class GoalProperty(BaseModel):
    name: str
    target_pv: float | None = None
    target_fv: float | None = None
    is_downpayment_only: bool = False
    upfront_amount: float | None = None
    goal_date: date
    inflation_annual: float | None = None
    mortgage_tenure_years: int = 0
    mortgage_interest_annual: float = 0.075

    @model_validator(mode="after")
    def _validate_goal_property(self) -> "GoalProperty":
        if self.target_pv is None and self.target_fv is None:
            raise ValueError("provide target_pv or target_fv (or both)")
        if self.is_downpayment_only:
            if self.upfront_amount is None:
                raise ValueError("upfront_amount required when is_downpayment_only=True")
            if self.mortgage_tenure_years <= 0:
                raise ValueError("mortgage_tenure_years must be > 0 when is_downpayment_only=True")
        return self


class GoalType(str, Enum):
    retirement = "retirement"
    property = "property"
    child_abroad_education = "child_abroad_education"
    child_local_education = "child_local_education"
    child_marriage = "child_marriage"
    custom = "custom"


class CustomGoal(BaseModel):
    name: str
    goal_type: GoalType
    amount_pv: float | None = None
    amount_fv: float | None = None
    goal_date: date
    inflation_rate_override: float | None = None

    @model_validator(mode="after")
    def _validate_custom_goal(self) -> "CustomGoal":
        if self.amount_pv is None and self.amount_fv is None:
            raise ValueError("provide amount_pv or amount_fv (or both)")
        return self


class OneOffEvent(BaseModel):
    description: str
    amount: float
    date: date


class GoalPlanningInput(BaseModel):
    assumptions: Assumptions = Field(default_factory=Assumptions)
    profile: ClientProfile
    retirement: RetirementInput
    current_properties: list[CurrentProperty] = []
    goal_properties: list[GoalProperty] = []
    custom_goals: list[CustomGoal] = []
    one_off_inflows: list[OneOffEvent] = []
    one_off_outflows: list[OneOffEvent] = []
    detail_level: Literal["default", "full"] = "default"

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "GoalPlanningInput":
        names: list[str] = ["retirement"]
        names.extend(p.name for p in self.current_properties)
        names.extend(p.name for p in self.goal_properties)
        names.extend(g.name for g in self.custom_goals)
        names.extend(e.description for e in self.one_off_inflows)
        names.extend(e.description for e in self.one_off_outflows)
        normalized = [n.casefold() for n in names]
        dupes = {n for n in normalized if normalized.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate names across inputs (case-insensitive): {sorted(dupes)}")
        return self


# ---------------------------------------------------------------------------
# Output types (engine → agent / public API)
# ---------------------------------------------------------------------------


class HeadlineStatus(BaseModel):
    horizon_years: int
    last_goal_date: date
    last_fy_end_date: date
    number_of_goals: int
    net_financial_assets_today: float
    sum_fund_today_pv: float
    present_status: float
    closing_nfa: float
    total_shortfall_fv: float
    total_funded_amount: float
    is_overall_feasible: bool
    overall_shortfall_pv: float
    overall_shortfall_fv: float


class RetirementSnapshot(BaseModel):
    retirement_date: date
    years_to_retirement: float
    annual_household_expense_at_retirement: float
    post_retirement_years: int
    real_roi_annual: float
    real_roi_monthly: float
    corpus_required_computed: float
    corpus_required_user_override: float | None
    corpus_required_used: float


class GoalFundingStatus(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    amount_pv: float
    amount_fv: float
    fund_today_pv: float
    funded_amount: float
    is_funded: bool
    shortfall_fv: float
    shortfall_pv: float
    expected_roi: float


class OneOffFundingStatus(BaseModel):
    description: str
    date: date
    amount: float
    funded_amount: float
    is_funded: bool
    shortfall: float


class AnnualCashflowRow(BaseModel):
    fy_end_date: date
    fy_label: str
    income: float
    income_tax: float
    household_expense: float
    savings_1: float
    existing_mortgage_emi_total: float
    goal_mortgage_emi_total: float
    savings_2: float
    one_off_in: float
    one_off_out: float
    investment_amount: float
    nfa_opening: float
    nfa_roi: float
    nfa_closing: float


class MonthlyCashflowRow(BaseModel):
    month_end_date: date
    fy_label: str
    income: float
    income_tax: float
    household_expense: float
    savings_1: float
    existing_mortgage_emi_total: float
    goal_mortgage_emi_total: float
    savings_2: float
    savings_2_avg: float


class MonthlyNFARow(BaseModel):
    month_end: date
    fy_label: str
    nfa_open: float
    regular_invest: float
    regular_invest_kind: Literal["user_sip", "savings_sip_fraction", "withdrawal", "zero"]
    roi: float
    one_off_in: float
    goal_outflow_total: float
    nfa_close: float
    savings_2_avg: float
    funded_flag: bool


class MortgageAmortizationRow(BaseModel):
    month_end: date
    opening_balance: float
    emi: float
    interest_portion: float
    principal_portion: float
    closing_balance: float


class MortgageAmortization(BaseModel):
    property_ref: str
    start_date: date
    monthly_schedule: list[MortgageAmortizationRow]


class FundFlowSummary(BaseModel):
    opening_nfa: float
    total_investments: float
    total_roi: float
    total_one_off_in: float
    total_one_off_out: float
    total_goals_paid: float
    closing_nfa: float


class ValidationIssue(BaseModel):
    field: str
    message: str
    severity: Literal["error", "warning"]


class GoalPlanningOutput(BaseModel):
    engine_version: str
    input_echo: GoalPlanningInput
    headline: HeadlineStatus
    retirement: RetirementSnapshot
    goals: list[GoalFundingStatus]
    one_off_outflow_status: list[OneOffFundingStatus]
    annual_cashflow: list[AnnualCashflowRow]
    fund_flow_summary: FundFlowSummary

    # Detail γ — populated only when detail_level == "full"
    monthly_cashflow: list[MonthlyCashflowRow] | None = None
    nfa_monthly_series: list[MonthlyNFARow] | None = None
    mortgage_amortizations: list[MortgageAmortization] | None = None

    warnings: list[str] = []
    computed_at: datetime


# ---------------------------------------------------------------------------
# Agent types (overrides, mutations, levers, NL extractor)
# ---------------------------------------------------------------------------


class NumericOverride(BaseModel):
    kind: Literal["numeric"]
    key: Literal[
        "monthly_investment_next_12m",
        "annual_income",
        "monthly_household_expense",
        "step_up_rate",
    ]
    value: float


class RateOverride(BaseModel):
    kind: Literal["rate"]
    key: Literal[
        "inflation_household_expense",
        "inflation_property",
        "inflation_child_abroad_education",
        "inflation_child_local_education",
        "inflation_child_marriage",
        "roi_long_term_post_tax",
        "roi_mid_term_post_tax",
        "roi_near_term_post_tax",
        "roi_retired_portfolio_annual",
    ]
    value: float


class PerGoalRateOverride(BaseModel):
    kind: Literal["per_goal_rate"]
    goal_name: str
    rate_kind: Literal["inflation"]
    value: float


class PropertyFieldOverride(BaseModel):
    kind: Literal["property_field"]
    property_name: str
    field: Literal[
        "mortgage_tenure_years",
        "mortgage_interest_annual",
        "upfront_amount",
        "is_downpayment_only",
        "goal_date",
        "early_payoff_date",
    ]
    value: float | int | bool | date


OverrideSpec = Annotated[
    Union[NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride],
    Field(discriminator="kind"),
]


class GoalMutation(BaseModel):
    kind: Literal["mutation"]
    op: Literal["add", "remove", "update"]
    goal_name: str
    fields: dict[str, Any] = {}


LeverAction = Annotated[
    Union[
        NumericOverride,
        RateOverride,
        PerGoalRateOverride,
        PropertyFieldOverride,
        GoalMutation,
    ],
    Field(discriminator="kind"),
]


class Lever(BaseModel):
    description: str
    action: LeverAction
    projected_outcome: HeadlineStatus
    confidence: Literal["low", "medium", "high"]


class ExtractedGoal(BaseModel):
    kind: Literal["custom_goal"]
    goal: CustomGoal


class ExtractedProperty(BaseModel):
    kind: Literal["property_goal"]
    property: GoalProperty
    assumptions_used: list[str] = []


class ExtractedCashflow(BaseModel):
    kind: Literal["cashflow_event"]
    event: OneOffEvent
    direction: Literal["in", "out"]
    confidence: Literal["high", "medium", "low"]


class ExtractedMutation(BaseModel):
    kind: Literal["goal_mutation"]
    op: Literal["add", "remove", "update"]
    goal_name: str
    fields: dict[str, Any] = {}


ExtractedFinancialEvent = Annotated[
    Union[ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation],
    Field(discriminator="kind"),
]


class ExtractionError(BaseModel):
    kind: Literal["error"]
    reason: str


class GoalPlanningResponse(BaseModel):
    engine_version: str
    output: GoalPlanningOutput | None
    narrative: str
    levers: list[Lever]
