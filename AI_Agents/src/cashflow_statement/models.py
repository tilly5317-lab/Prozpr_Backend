"""Public Pydantic contracts for the cashflow_statement module.

All types here cross the engine↔agent boundary or are part of the public API
exported from cashflow_statement/__init__.py.
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
    default_mortgage_tenure_years: int = 20
    default_mortgage_interest_annual: float = 0.075


class ClientProfile(BaseModel):
    annual_income: float
    effective_tax_rate: float = Field(
        description=(
            "Average post-deduction blended income-tax rate, applied as "
            "`gross_income × effective_tax_rate`. Typical: 0.10–0.25 salaried, "
            "0.20–0.30 HNI. Do NOT pass marginal slab rate."
        ),
    )
    financial_assets: float
    financial_liabilities_excl_mortgage: float
    monthly_household_expense: float
    starting_monthly_investment: float | None = None


class RetirementInput(BaseModel):
    date_of_birth: date
    retirement_age: int = 60
    assumed_lifespan_years: int = 85
    retirement_date_override: date | None = None
    retirement_corpus_pv_today_override: float | None = Field(
        default=None,
        description=(
            "Optional corpus target in TODAY's ₹. Engine inflates this by "
            "`inflation_household_expense` to retirement date for the goal."
        ),
    )


class CurrentProperty(BaseModel):
    """Existing property with optional mortgage (Option X: trust user's EMI + end date).

    When `has_mortgage=True`, both `mortgage_emi` and `mortgage_end_date` are required.
    The engine projects EMI outflows from today through `mortgage_end_date` per FY.
    """
    name: str
    has_mortgage: bool
    mortgage_emi: float | None = Field(
        default=None,
        description="Current monthly EMI in ₹.",
    )
    mortgage_end_date: date | None = Field(
        default=None,
        description="Date on which the final EMI is paid (last installment).",
    )

    @model_validator(mode="after")
    def _validate_mortgage_fields(self) -> "CurrentProperty":
        if self.has_mortgage:
            missing = [
                f for f in ("mortgage_emi", "mortgage_end_date")
                if getattr(self, f) is None
            ]
            if missing:
                raise ValueError(
                    f"current_property:{self.name} has_mortgage=True requires: {missing}"
                )
        return self


class GoalProperty(BaseModel):
    name: str
    target_pv: float | None = None
    target_fv: float | None = None
    is_downpayment_only: bool = False
    upfront_amount: float | None = None
    downpayment_pct: float | None = Field(
        default=None,
        description=(
            "Downpayment as fraction of target_fv (0.0–1.0). Mutually exclusive "
            "with `upfront_amount` when `is_downpayment_only=True`."
        ),
    )
    goal_date: date
    inflation_annual: float | None = None
    mortgage_tenure_years: int | None = None  # falls back to assumptions.default_mortgage_tenure_years
    mortgage_interest_annual: float | None = None  # falls back to assumptions.default_mortgage_interest_annual

    @model_validator(mode="after")
    def _validate_goal_property(self) -> "GoalProperty":
        if self.target_pv is None and self.target_fv is None:
            raise ValueError("provide target_pv or target_fv (or both)")
        if self.is_downpayment_only:
            # XOR: exactly one of (upfront_amount, downpayment_pct) must be set
            both_set = self.upfront_amount is not None and self.downpayment_pct is not None
            neither_set = self.upfront_amount is None and self.downpayment_pct is None
            if both_set:
                raise ValueError(
                    "provide exactly one of upfront_amount or downpayment_pct, not both"
                )
            if neither_set:
                raise ValueError(
                    "upfront_amount or downpayment_pct required when is_downpayment_only=True"
                )
            if self.downpayment_pct is not None and not (0.0 <= self.downpayment_pct <= 1.0):
                raise ValueError("downpayment_pct must be between 0.0 and 1.0")
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
    goal_value_pv: float | None = None
    goal_value_fv: float | None = None
    goal_date: date
    inflation_rate_override: float | None = Field(
        default=None,
        description=(
            "Per-goal inflation rate override. When None, the engine falls back to "
            "the rate from `Assumptions` keyed by `goal_type` "
            "(`inflation_property` / `inflation_child_*` / `inflation_household_expense` for `custom`)."
        ),
    )

    @model_validator(mode="after")
    def _validate_custom_goal(self) -> "CustomGoal":
        if self.goal_value_pv is None and self.goal_value_fv is None:
            raise ValueError("provide goal_value_pv or goal_value_fv (or both)")
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
    """Top-line metrics. Every field is Excel-traceable (cell IDs in field comments).

    Engine-only fields (`is_overall_feasible`, `overall_shortfall_*`) were
    dropped — callers can recompute feasibility from `all(g.is_funded) and corpus_closing >= 0`.
    """
    years_to_last_goal: int             # B89
    last_goal_date: date                # B87
    last_fy_end_date: date              # B88
    number_of_goals: int                # B86
    corpus_today: float   # B26
    total_corpus_required_today: float  # O113 — PV today of all goals combined
    surplus_or_shortfall_today: float   # S105 — corpus today minus PV of all goals
    corpus_closing: float                  # S214 — corpus at end of projection horizon
    total_shortfall_fv: float           # L113 — sum of per-goal shortfalls (FV)
    total_funded_amount: float          # M113 — sum of per-goal funded amounts


class RetirementSnapshot(BaseModel):
    retirement_date_computed: date                 # DOB + retirement_age (natural)
    retirement_date: date                          # the one actually used (= override if set, else computed)
    years_to_retirement: float
    annual_household_expense_today: float          # PV (= monthly_household_expense × 12)
    annual_household_expense_at_retirement: float  # FV at retirement_date
    post_retirement_years: int
    real_roi_annual: float
    corpus_required_computed: float                # FV at retirement_date
    corpus_required_user_override: float | None    # FV (inflated from user PV input)
    corpus_required_used: float                    # FV (whichever of the above is in effect)
    corpus_required_pv_today: float                # PV in today's ₹ (back-discounted from `_used`)


class GoalFundingStatus(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    goal_value_pv: float        # full goal value in today's ₹
    goal_value_fv: float        # full goal value at goal_date (inflated)
    corpus_required_fv: float   # corpus drain at goal_date (= goal_value_fv unless mortgaged property)
    investment_required_pv: float
    funded_amount: float
    is_funded: bool
    shortfall_fv: float
    expected_roi: float


class OneOffFundingStatus(BaseModel):
    description: str
    date: date
    amount: float
    funded_amount: float
    is_funded: bool
    shortfall: float


class AnnualCashflowRow(BaseModel):
    """Per-FY rollup of MonthlyCashflowRow — both P&L sums and corpus evolution.

    P&L fields (income through one_off_outflow) are pure column sums.
    corpus fields:
      - corpus_opening   = first month's corpus_opening in the FY
      - corpus_closing   = last month's corpus_closing in the FY
      - monthly_investment / investment_returns / goal_payout = column sums
      - is_funded = True only if every month in the FY was funded

    Per-row reconciliation (mirrors the monthly row):
      corpus_closing == corpus_opening + monthly_investment + investment_returns
                   + one_off_inflow - goal_payout - one_off_outflow
    """
    fy_end_date: date
    fy_label: str
    # --- P&L side ----------------------------------------------------------
    income: float
    income_tax: float
    household_expense: float
    savings_pre_emi: float
    existing_mortgage_emi: float
    goal_mortgage_emi: float
    savings_post_emi: float
    one_off_inflow: float
    one_off_outflow: float
    # --- corpus evolution -----------------------------------------------------
    # Defaults so the model can still be constructed in tests that don't run funding.
    corpus_opening: float = 0.0
    monthly_investment: float = 0.0
    investment_returns: float = 0.0
    goal_payout: float = 0.0
    corpus_closing: float = 0.0
    is_funded: bool = True


class MonthlyCashflowRow(BaseModel):
    """One row per month — combined household cashflow + corpus evolution view.

    Built in two passes: project_cashflow() fills the P&L fields (income through
    one_off_outflow); compute_funding() fills the corpus fields (corpus_opening through
    is_funded) by copying each row and updating those fields. Both views are
    keyed on month_end_date so they always align.

    Reconciliation per row:
      corpus_closing == corpus_opening + monthly_investment + investment_returns
                   + one_off_inflow - goal_payout - one_off_outflow
    """
    month_end_date: date
    fy_label: str
    # --- P&L side (from project_cashflow) -----------------------------------
    income: float
    income_tax: float
    household_expense: float
    savings_pre_emi: float
    existing_mortgage_emi: float
    goal_mortgage_emi: float
    savings_post_emi: float
    one_off_inflow: float = 0.0
    one_off_outflow: float = 0.0
    # --- Balance side (from compute_funding) --------------------------------
    # Defaults let project_cashflow build the row before funding fills these in.
    corpus_opening: float = 0.0
    monthly_investment: float = 0.0
    investment_source: Literal[
        "user_sip", "user_sip_capped", "savings_sip_fraction", "withdrawal", "zero"
    ] = "zero"
    investment_returns: float = 0.0
    goal_payout: float = 0.0  # Goal payouts ONLY (one-off outflows live in one_off_outflow).
    corpus_closing: float = 0.0
    is_funded: bool = True


class FundFlowSummary(BaseModel):
    """Bridge totals across the projection horizon, plus the present-value
    "goal funding status" snapshot.

    The horizon bridge reconciles as:
      corpus_opening + total_investments + total_roi + total_one_off_in
        - total_one_off_out - total_goals_paid = corpus_closing
    where total_one_off_out and total_goals_paid are stored as positive
    magnitudes (the bridge subtracts them).

    The goal-funding-status fields mirror HeadlineStatus so consumers of
    FundFlowSummary get the PV view without a second model.
    """
    # --- Horizon bridge -----------------------------------------------------
    corpus_opening: float          # B26
    total_investments: float    # S94 — signed sum of monthly_investment (negative = withdrawal)
    total_roi: float            # S95
    total_one_off_in: float     # S96 — positive magnitude
    total_one_off_out: float    # -S97 — positive magnitude (Excel stores as negative)
    total_goals_paid: float     # -S98 — positive magnitude (Excel stores as negative)
    corpus_closing: float          # S214

    # --- Goal funding status (present-value view; mirrors HeadlineStatus) ---
    corpus_today: float           # = corpus_opening; kept as its own field for clarity
    total_corpus_required_today: float          # PV today of all goals
    surplus_or_shortfall_today: float           # corpus_today - total_corpus_required_today


class ValidationIssue(BaseModel):
    field: str
    message: str
    severity: Literal["error", "warning"]


class GoalBullet(BaseModel):
    """One per goal — the LLM verdict line a customer-facing UI / chat can show as-is."""
    name: str
    verdict: Literal["funded", "partially_funded", "unfunded"]
    headline_amount: str = Field(
        description=(
            "Pre-formatted Indian-notation rupee string (e.g. '₹1.25 crore'). "
            "For funded goals: the corpus required. For shortfalls: the gap."
        ),
    )
    note: str = Field(description="One sentence on why this goal lands here.")


class PlanSummary(BaseModel):
    """LLM-generated narrative summary of a `GoalPlanningOutput`.

    Designed as a handoff payload to a customer-facing LLM: every rupee value is
    already in Indian notation (₹X.XX lakh / crore) so the consumer never has to
    convert and can quote the strings verbatim.
    """
    top_line: str = Field(
        description="1-2 sentence overall verdict — funded vs shortfall, biggest driver.",
    )
    retirement_note: str = Field(
        description="1 sentence on retirement adequacy.",
    )
    goals: list[GoalBullet]
    cashflow_note: str = Field(
        description="1 sentence on income vs. expense / EMI / SIP capacity.",
    )
    risks: list[str] = Field(
        description="Bulleted concerns (e.g. concentration, near-term shortfall, EMI burden).",
    )
    next_steps: list[str] = Field(
        description="Concrete actions the user could explore (e.g. increase SIP, defer goal).",
    )


class GoalPlanningOutput(BaseModel):
    engine_version: str
    input_echo: GoalPlanningInput
    headline: HeadlineStatus
    retirement: RetirementSnapshot
    goals: list[GoalFundingStatus]
    one_off_outflow_status: list[OneOffFundingStatus]
    annual_cashflow: list[AnnualCashflowRow]
    fund_flow_summary: FundFlowSummary
    goal_property_details: list[GoalPropertyDetail] = []
    # DerivedStats deleted entirely (engine-only, no Excel parallel).

    # Detail γ — populated only when detail_level == "full".
    # monthly_cashflow is the combined cashflow + corpus monthly view (see MonthlyCashflowRow).
    monthly_cashflow: list[MonthlyCashflowRow] | None = None

    warnings: list[str] = []
    computed_at: datetime


# ---------------------------------------------------------------------------
# Agent types (overrides, mutations, levers, NL extractor)
# ---------------------------------------------------------------------------


class NumericOverride(BaseModel):
    kind: Literal["numeric"]
    key: Literal[
        "starting_monthly_investment",
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
        "downpayment_pct",
        "is_downpayment_only",
        "goal_date",
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


class TurnAction(BaseModel):
    """Audit log entry: which tool was called this turn, with what args, and the summary it returned."""
    tool_name: str
    arguments: dict[str, Any] = {}
    summary: str


class GoalPropertyDetail(BaseModel):
    """Public-facing property goal details (lifts info from internal GoalPropertyOutcome)."""
    name: str
    target_pv: float
    target_fv: float
    corpus_required_fv: float                 # what gets paid at goal_date (= upfront FV if mortgage, else target FV)
    is_downpayment_only: bool
    upfront_amount: float | None = None      # user PV input (None if downpayment_pct used)
    downpayment_pct: float | None = None     # user fraction input (None if upfront_amount used)
    mortgage_amount: float                  # 0 if cash purchase
    mortgage_tenure_years: int               # resolved (user override or assumption default)
    mortgage_interest_annual: float          # resolved (user override or assumption default)
    mortgage_emi_monthly: float | None = None
    mortgage_total_interest: float | None = None
    mortgage_payoff_date: date | None = None
    goal_date: date


# DerivedStats + GoalCategoryAggregate deleted (engine-only, no Excel parallel).
# Callers wanting peak/min corpus, debt-free date, etc. should scan corpus_monthly_series directly.


class ExtractedGoal(BaseModel):
    kind: Literal["custom_goal"]
    goal: CustomGoal

    def dated_field(self) -> date | None:
        return self.goal.goal_date


class ExtractedProperty(BaseModel):
    kind: Literal["property_goal"]
    property: GoalProperty
    assumptions_used: list[str] = []

    def dated_field(self) -> date | None:
        return self.property.goal_date


class ExtractedCashflow(BaseModel):
    kind: Literal["cashflow_event"]
    event: OneOffEvent
    direction: Literal["in", "out"]
    confidence: Literal["high", "medium", "low"]

    def dated_field(self) -> date | None:
        return self.event.date


class ExtractedMutation(BaseModel):
    kind: Literal["goal_mutation"]
    op: Literal["add", "remove", "update"]
    goal_name: str
    fields: dict[str, Any] = {}

    def dated_field(self) -> date | None:
        v = self.fields.get("goal_date")
        return v if isinstance(v, date) else None


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


class GoalPlanningRequest(BaseModel):
    """What the responder passes in. (Used by agent refactor; kept here for type completeness now.)"""
    user_question: str
    baseline_input: GoalPlanningInput
    chat_session_id: str
    anchor_date: date
    detail_level: Literal["default", "full"] = "default"


class GoalPlanningSnapshot(GoalPlanningOutput):
    """Agent return = engine output + per-turn metadata.
    Inherits all fields of GoalPlanningOutput and adds turn-level info.
    """
    extracted_events_this_turn: list[ExtractedFinancialEvent] = []
    actions_taken_this_turn: list[TurnAction] = []
    levers: list[Lever] = []
    validation_issues: list[ValidationIssue] = []
    error_log: list[str] = []
    summary: PlanSummary | None = None  # LLM narrative, written by the agent's finalize node
