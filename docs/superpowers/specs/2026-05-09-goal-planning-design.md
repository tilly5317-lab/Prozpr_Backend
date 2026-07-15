# Goal Planning Module — Design Spec

**Status:** Draft for v1 implementation
**Date:** 2026-05-09
**Module path:** `AI_Agents/src/goal_planning/`
**Sibling library:** `AI_Agents/src/financial_primitives/`
**Excel reference:** `Local_logics/Sourabh_Logics/goal_based_allocation_model (10).xlsx`, sheet `Goal planning`

---

## TL;DR

A new AI module `goal_planning` that answers: *"are the user's financial goals feasible, and if not, how do we close the gap?"* It is composed of (1) a deterministic **engine** that mirrors Sourabh's Excel model at parity (~30 calcs across retirement, properties, mortgages, cashflow projection, and per-goal funding allocation), and (2) a **LangGraph agent** that wraps the engine with NL goal capture, what-if rerun, recommendations, and Q&A. The module is self-contained — bridge integration into `app/services/ai_bridge/goal_planning/` and `app/services/chat_core/brain.py` is documented but **out of scope for this module's v1**.

---

## 1. Context and motivation

The product needs an AI module that helps users plan their financial future: retirement, property purchases (with mortgages), child education, marriage, generic life goals, plus one-off cashflows. Sourabh has built a comprehensive Excel model that does this deterministically. This module ports that Excel to Python, wraps it in a tool-calling agent, and exposes it through the project's existing chat-handler dispatch pattern.

Existing AI modules (`asset_allocation_pydantic`, `rebalancing`, `intent_classifier`, `portfolio_query`, `risk_profiling`, `market_commentary`) all follow a deterministic-pipeline + optional-LLM pattern. `goal_planning` is the first module that uses a full **LangGraph tool-calling agent** because user interactions inherently span multiple turns (capture goals → run projection → ask what-if → propose fix → re-run).

The chat layer already has a stub for `intent == "goal_planning"` at `app/services/chat_core/brain.py:210-217` that returns a redirect. This stub is replaced once integration ships.

---

## 2. Goals and non-goals

### v1 goals
- **Excel parity** for the deterministic engine. The engine output for Sourabh's Excel-as-baseline must match within tolerance.
- **Tool-calling agent** with 6 tools (extract, apply_override, clear_overrides, mutate_goal, compute_projection, propose_levers).
- **NL goal capture** via a single consolidated extractor returning a discriminated union over 4 kinds.
- **Q&A** over an existing projection (no recompute on read-only queries).
- **What-if** support via override staging + rerun.
- **Recommendations**: 7 deterministic lever types — A (increase SIP), B (defer goal), C (reduce target), D (change retirement age), E (increase step-up rate), F (reduce household expense), G (pay off existing mortgage early).
- **Excel parity scenarios:** scenario #01 (Sourabh's as-is) plus 02–04 minimum (no_mortgages, already_retired, overfunded) authored before v1 ships.
- **Self-contained module:** zero new app schema; checkpointer manages its own table.

### v1 non-goals (deferred to v2)
- Synthetic parity test #11 (multi-existing-mortgage roll-up).
- Excel parity scenarios 05–10 (huge_shortfall, property_only, cashflow_heavy, minimum_viable, single_retirement_goal, zero_income, plus stress-extremes).
- Agent ability to toggle `detail_level` (always uses `"default"` in v1).
- Vectorized mortgage amortization (perf optimization).
- LangSmith tracing on by default in production.
- `extract_existing_mortgage_from_nl` (existing mortgages captured at onboarding, not chat).
- `compare_scenarios` and `save_plan_snapshot` tools.
- `mutmut` mutation testing (stays optional always).
- Bridge layer (`app/services/ai_bridge/goal_planning/`), DB schema additions, and `brain.py` stub replacement — done in a separate integration phase.

### Explicitly out of scope (forever)
- Investment advice (only feasibility projection).
- Asset allocation decisions (handled by `asset_allocation_pydantic`).
- Tax optimization (handled by `tax_pydantic` if/when it ships).

---

## 3. Glossary

| Term | Meaning |
|---|---|
| NFA | Net Financial Assets = `financial_assets − financial_liabilities_excl_mortgage` |
| FY | Indian Financial Year (Apr 1 → Mar 31) |
| PV / FV | Present Value / Future Value |
| EMI | Equated Monthly Installment |
| Goal | Any financial objective with an amount and date (retirement, property, education, marriage, custom) |
| Funded amount | Σ allocated savings + ROI accrued for a goal by its `goal_date` |
| Shortfall (FV) | `amount_fv − funded_amount` for a goal; clamped to zero if funded |
| Present status | `NFA_today − Σ fund_today_pv` (positive = on track today; negative = currently underfunded) |
| Closing NFA | Last-period NFA value in the projection horizon |
| Total shortfall | Σ per-goal shortfall_fv across all goals |
| Lever | A deterministic candidate fix (e.g., increase SIP) that the engine pre-validates by re-running the projection |
| Override | A what-if change to a non-goal parameter (rate, income, expense) staged via `apply_override` |
| Mutation | A change to a goal (add/remove/update amount/update date) staged via `mutate_goal` |

---

## 4. Architecture overview

### Three layers

```
┌─────────────────────────────────────────────────────────┐
│ Bridge layer (out of scope)                             │
│ app/services/ai_bridge/goal_planning/                   │
│   ↳ input_builder.py (User ORM → GoalPlanningInput)     │
│   ↳ chat.py (@register("goal_planning") handler)        │
└──────────────┬──────────────────────────────────────────┘
               │ run_cashflow_statement_agent(...)
               ▼
┌─────────────────────────────────────────────────────────┐
│ Agent layer — AI_Agents/src/goal_planning/agent/        │
│   • LangGraph ReAct loop (Sonnet 4.6)                   │
│   • 6 tools, AgentState, checkpointer                   │
│   • NL extractor (Haiku 4.5)                            │
│   • 7 deterministic levers (A–G)                        │
└──────────────┬──────────────────────────────────────────┘
               │ compute_full_projection(GoalPlanningInput)
               ▼
┌─────────────────────────────────────────────────────────┐
│ Engine layer — AI_Agents/src/goal_planning/engine/      │
│   • 8-stage deterministic pipeline                      │
│   • 30 calculations across 11 submodules                │
│   • Zero LLM imports                                    │
│   ↳ uses AI_Agents/src/financial_primitives/            │
└─────────────────────────────────────────────────────────┘
```

### LLM call rule
All Claude calls go through `langchain-anthropic` (`ChatAnthropic` directly or via LCEL chains), per project CLAUDE.md. The engine has zero LLM imports (enforced by AST lint test). The agent uses Sonnet 4.6 for the loop and Haiku 4.5 for the extractor.

### State strategy
LangGraph **checkpointer** (`MemorySaver` for tests, `AsyncPostgresSaver` for prod) keyed by `thread_id = str(session_id)`. No new app schema; checkpointer manages its own table. `accumulated_overrides`, `captured_goals`, `captured_properties`, `captured_cashflows`, `last_output` persist across turns natively.

---

## 5. Package layout

```
AI_Agents/src/
├── financial_primitives/           # Shared library — pure Python, zero LLM
│   ├── __init__.py
│   ├── time_value.py               # future_value, present_value, compound
│   ├── annuity.py                  # pmt, rate (Newton-Raphson), ipmt
│   ├── inflation.py                # inflate, real_rate
│   ├── retirement.py               # retirement_corpus_pv (composite)
│   ├── dates.py                    # fy_for_date, fy_end, EOMONTH
│   ├── tools.py                    # OPTIONAL @tool wrappers (lazy import)
│   └── tests/
│
└── goal_planning/
    ├── __init__.py                 # public API (re-exports from engine + agent + models)
    ├── models.py                   # public Pydantic contracts
    ├── prompts.py                  # system prompts (top-level shared, agent-specific in agent/prompts.py)
    │
    ├── engine/                     # 13 files; deterministic; no LLM
    │   ├── __init__.py             # exports compute_full_projection, ENGINE_VERSION, validate_input_only
    │   ├── pipeline.py             # 8-stage orchestrator
    │   ├── _types.py               # engine-private intermediates
    │   ├── exceptions.py           # MissingDOBError, PastGoalDateError, RATEConvergenceError
    │   ├── profile.py              # build_initial_context → RunContext
    │   ├── dates.py                # FY math, real_roi, _round_thousand
    │   ├── retirement.py           # RetirementSnapshot computation
    │   ├── mortgages.py            # RATE inversion, PMT, IPMT, schedules with first-FY proration
    │   ├── properties.py           # goal-property FV + mortgage assembly
    │   ├── goals_table.py          # unified goals list, expected_roi 3-band, fund_today_pv
    │   ├── cashflow.py             # monthly + annual projection, savings_2_avg per FY
    │   ├── funding.py              # shared NFA pool + proportional shortfall + M147 4-branch
    │   └── summary.py              # HeadlineStatus + FundFlowSummary + feasibility
    │
    ├── agent/                      # 8 files; LangGraph; all LLM calls
    │   ├── __init__.py             # exports cashflow_statement_graph, run_cashflow_statement_agent
    │   ├── state.py                # AgentState TypedDict + CapturedCashflow
    │   ├── graph.py                # StateGraph + checkpointer wiring
    │   ├── nodes.py                # ingest_baseline_node, agent_node, should_continue
    │   ├── tools.py                # 6 @tool definitions (InjectedState pattern)
    │   ├── extractor.py            # NL → discriminated union (Haiku)
    │   ├── levers.py               # 7 deterministic lever generators (A–G)
    │   └── prompts.py              # system prompt, fallback messages
    │
    ├── config.py                   # module constants (no BaseSettings)
    │
    └── tests/                      # module-private tests
        ├── conftest.py
        ├── unit/                   # ~80 tests across engine submodules
        ├── integration/            # ~20 tests; engine pipeline, parity
        ├── agent/                  # ~30 tests; tools, levers, e2e w/ FakeChatAnthropic
        ├── boundary/               # 2 AST lint tests
        └── fixtures/
            ├── excel_reference/    # scenario_01 + 02-04
            ├── synthetic/          # 13 closed-form cases
            └── llm_mocks/          # canned LLM responses
```

Cross-agent test suites live at `AI_Agents/tests/` (unchanged); `goal_planning/tests/` is module-private.

### Boundary invariants
- `engine/` has zero imports from `langchain_anthropic`, `anthropic` (including exceptions), `langchain_core`, `langgraph`. Enforced by AST lint test.
- `agent/` imports from `..engine` and `..models` only. Cannot reach into `engine/_types.py` or `engine/exceptions.py` privately.
- Bridge code (`app/services/ai_bridge/goal_planning/`, future) imports only from top-level `goal_planning`. Cannot import `goal_planning.engine.*` or `goal_planning.agent.*`. Enforced by AST lint test.

---

## 6. Public Pydantic contracts (`models.py`)

### 6.1 Input

```python
class Assumptions(BaseModel):
    inflation_property: float = 0.06
    inflation_child_abroad_education: float = 0.08      # =3%+5% from Excel
    inflation_child_local_education: float = 0.06
    inflation_child_marriage: float = 0.06
    inflation_household_expense: float = 0.06           # used pre- AND post-retirement
    annual_income_growth: float = 0.08                  # B8
    annual_invested_amount_growth: float = 0.08         # B9
    roi_near_term_post_tax: float = 0.05                # B11
    roi_mid_term_post_tax: float = 0.07                 # B12
    roi_long_term_post_tax: float = 0.09                # B13
    roi_retired_portfolio_annual: float = 0.09          # B14
    near_term_horizon_years: int = 2                    # cutoff: B20
    medium_term_horizon_years: int = 3                  # extends to: B21
    default_mortgage_tenure_years: int = 30             # used as default for current_properties only
    default_mortgage_interest_annual: float = 0.075     # 2026 fallback if RATE inversion fails


class ClientProfile(BaseModel):
    latest_update_date: date                            # B18 — anchor for all date math
    annual_income: float                                # B22
    tax_rate: float                                     # B23
    financial_assets: float                             # B24
    financial_liabilities_excl_mortgage: float          # B25
    monthly_household_expense: float                    # source for B41 = monthly × 12
    monthly_investment_next_12m: float | None = None    # B27; None ≠ 0 (None triggers fallback)


class RetirementInput(BaseModel):
    date_of_birth: date
    retirement_age: int = 60
    assumed_total_age: int = 85                         # lifespan
    retirement_date_override: date | None = None
    retirement_corpus_pv_override: float | None = None  # B44


class CurrentProperty(BaseModel):
    name: str
    has_mortgage: bool
    mortgage_balance: float | None = None
    mortgage_emi: float | None = None
    mortgage_last_date: date | None = None
    mortgage_balance_as_of_date: date | None = None     # B59 per-property; defaults to profile.latest_update_date


class GoalProperty(BaseModel):
    name: str
    target_pv: float | None = None                      # exactly one of pv/fv required
    target_fv: float | None = None
    is_downpayment_only: bool = False                   # default False = cash purchase
    upfront_amount: float | None = None                 # required iff is_downpayment_only=True
    goal_date: date
    inflation_annual: float | None = None               # default → assumptions.inflation_property
    mortgage_tenure_years: int = 0                      # 0 = no mortgage
    mortgage_interest_annual: float = 0.075

    @model_validator(mode="after")
    def _validate(self) -> "GoalProperty":
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
    amount_pv: float | None = None                      # exactly one of pv/fv required
    amount_fv: float | None = None
    goal_date: date
    inflation_rate_override: float | None = None        # else looked up by goal_type

    @model_validator(mode="after")
    def _validate(self) -> "CustomGoal":
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
        names = ["retirement"]                           # reserved
        names.extend(p.name for p in self.current_properties)
        names.extend(p.name for p in self.goal_properties)
        names.extend(g.name for g in self.custom_goals)
        names.extend(e.description for e in self.one_off_inflows)
        names.extend(e.description for e in self.one_off_outflows)
        normalized = [n.casefold() for n in names]      # case-insensitive
        dupes = {n for n in normalized if normalized.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate names across inputs (case-insensitive): {dupes}")
        return self
```

### 6.2 Output

```python
class HeadlineStatus(BaseModel):
    horizon_years: int
    last_goal_date: date
    last_fy_end_date: date                              # B88 — includes one_off_out tail
    number_of_goals: int
    net_financial_assets_today: float                   # B26
    sum_fund_today_pv: float                            # O113
    present_status: float                               # NFA − sum_fund_today_pv
    closing_nfa: float                                  # last monthly_cashflow row's nfa_close
    total_shortfall_fv: float                           # Σ per-goal shortfall_fv (positive convention)
    total_funded_amount: float                          # M113 = Σ amount_fv − total_shortfall
    is_overall_feasible: bool                           # all funded ∧ present_status ≥ 0 ∧ min_nfa_in_horizon ≥ 0
    overall_shortfall_pv: float
    overall_shortfall_fv: float


class RetirementSnapshot(BaseModel):
    retirement_date: date
    years_to_retirement: float
    annual_household_expense_at_retirement: float       # B42 (FV)
    post_retirement_years: int
    real_roi_annual: float
    real_roi_monthly: float                             # B15
    corpus_required_computed: float                     # B43
    corpus_required_user_override: float | None         # B44
    corpus_required_used: float                         # B46


class GoalFundingStatus(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    amount_pv: float
    amount_fv: float                                    # for property: payout_amount_fv (upfront-only if mortgage)
    fund_today_pv: float
    funded_amount: float                                # = amount_fv − shortfall_fv
    is_funded: bool
    shortfall_fv: float                                 # positive convention; 0 if funded
    shortfall_pv: float                                 # discounted at expected_roi
    expected_roi: float                                 # 3-band horizon lookup


class OneOffFundingStatus(BaseModel):
    description: str
    date: date
    amount: float
    funded_amount: float
    is_funded: bool
    shortfall: float


class AnnualCashflowRow(BaseModel):
    fy_end_date: date
    fy_label: str                                       # "FY27"
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
    savings_2_avg: float                                # K147 — FY-bucket average


class MonthlyNFARow(BaseModel):
    month_end: date
    fy_label: str
    nfa_open: float
    regular_invest: float                               # M147
    regular_invest_kind: Literal["user_sip", "savings_sip_fraction", "withdrawal", "zero"]
    roi: float                                          # 2-band: near vs long
    one_off_in: float
    goal_outflow_total: float                           # Q147 — goals + one_off_out
    nfa_close: float
    savings_2_avg: float
    funded_flag: bool                                   # T147


class MortgageAmortizationRow(BaseModel):
    month_end: date
    opening_balance: float
    emi: float
    interest_portion: float
    principal_portion: float
    closing_balance: float


class MortgageAmortization(BaseModel):
    property_ref: str                                   # "existing:apartment_1" or "goal:second_home"
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
    engine_version: str                                 # = ENGINE_VERSION
    input_echo: GoalPlanningInput                       # frozen snapshot of what was used
    headline: HeadlineStatus
    retirement: RetirementSnapshot
    goals: list[GoalFundingStatus]                      # ordered by goal_date
    one_off_outflow_status: list[OneOffFundingStatus]
    annual_cashflow: list[AnnualCashflowRow]
    fund_flow_summary: FundFlowSummary

    # Detail γ — populated only when detail_level == "full"
    monthly_cashflow: list[MonthlyCashflowRow] | None = None
    nfa_monthly_series: list[MonthlyNFARow] | None = None
    mortgage_amortizations: list[MortgageAmortization] | None = None

    warnings: list[str] = []
    computed_at: datetime
```

### 6.3 Agent types

```python
# Override types — for non-goal parameters only (post-Q3 trim)
class NumericOverride(BaseModel):
    kind: Literal["numeric"]
    key: Literal[
        "monthly_investment_next_12m",
        "annual_income",
        "monthly_household_expense",
        "step_up_rate",                                 # = annual_invested_amount_growth
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
    property_name: str                                  # matches current_properties[*].name OR goal_properties[*].name
    field: Literal[
        # Goal-property fields
        "mortgage_tenure_years",
        "mortgage_interest_annual",
        "upfront_amount",
        "is_downpayment_only",
        "goal_date",
        # Existing-property fields (used by Lever G)
        "early_payoff_date",                            # date — wipe existing mortgage_balance to 0 at this date
    ]
    value: float | int | bool | date


OverrideSpec = Annotated[
    Union[NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride],
    Field(discriminator="kind"),
]


# Goal mutations — for goal-level changes (incl. retirement per Q3)
class GoalMutation(BaseModel):
    kind: Literal["mutation"]
    op: Literal["add", "remove", "update"]
    goal_name: str                                      # "retirement" allowed
    fields: dict[str, Any] = {}                         # validated per goal_type at apply time


# Lever — what propose_levers returns
LeverAction = Annotated[
    Union[NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride, GoalMutation],
    Field(discriminator="kind"),
]


class Lever(BaseModel):
    description: str                                    # "Increase monthly SIP from ₹50,000 to ₹65,000"
    action: LeverAction
    projected_outcome: HeadlineStatus                   # what happens after applying it
    confidence: Literal["low", "medium", "high"]


# Extractor types — discriminated union of NL extraction results
class ExtractedGoal(BaseModel):
    kind: Literal["custom_goal"]
    goal: CustomGoal


class ExtractedProperty(BaseModel):
    kind: Literal["property_goal"]
    property: GoalProperty
    assumptions_used: list[str] = []                    # disclosed in narrative


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


# Top-level agent response
class GoalPlanningResponse(BaseModel):
    engine_version: str
    output: GoalPlanningOutput | None                   # None for pure Q&A turns
    narrative: str
    levers: list[Lever]
```

### 6.4 Validators (cross-input)

The `_validate_unique_names` validator on `GoalPlanningInput` enforces case-insensitive uniqueness across:
- Reserved name `"retirement"`
- `current_properties[*].name`
- `goal_properties[*].name`
- `custom_goals[*].name`
- `one_off_inflows[*].description`
- `one_off_outflows[*].description`

Duplicates raise `ValueError` at engine entry, before stage 1.

---

## 7. Engine subdivision

### 7.1 File map (13 files)

| File | Responsibility |
|---|---|
| `__init__.py` | exports `compute_full_projection`, `validate_input_only`, `ENGINE_VERSION` |
| `pipeline.py` | 8-stage orchestrator |
| `_types.py` | engine-private intermediates (`RunContext`, `MortgageSchedule`, etc.) |
| `exceptions.py` | `MissingDOBError`, `PastGoalDateError`, `RATEConvergenceError` |
| `profile.py` | `build_initial_context` → `RunContext`; `RunContext.with_retirement` |
| `dates.py` | FY math, EOMONTH, real_roi, **`_round_thousand`** helper |
| `retirement.py` | `compute_retirement_snapshot` (corpus PV calc, override path, already-retired branch) |
| `mortgages.py` | `invert_rate` (Newton-Raphson), `compute_emi` (PMT), `build_amortization` (monthly + annual), first-FY proration, IPMT for goal-mortgage interest |
| `properties.py` | `build_goal_properties` (FV, downpayment vs cash branch, mortgage assembly) |
| `goals_table.py` | `build_goals_table`, `expected_roi_for_goal` (3-band), `fund_today_pv`; retirement skips inflation lookup |
| `cashflow.py` | `project_cashflow` (monthly + annual + savings_2_avg per FY), `compute_horizon_years` (cap = 80) |
| `funding.py` | `compute_funding` (shared NFA pool, M147 4-branch rule, proportional shortfall allocation) |
| `summary.py` | `build_headline_status`, `build_fund_flow_summary` |

### 7.2 8-stage orchestrator

```python
def compute_full_projection(input: GoalPlanningInput) -> GoalPlanningOutput:
    warnings: list[str] = []

    ctx = build_initial_context(input.profile, input.assumptions)               # 1
    retirement = compute_retirement_snapshot(input.retirement, ctx, warnings)   # 2a
    ctx = ctx.with_retirement(retirement)                                       # 2b — immutable update

    existing_mortgages = build_existing_mortgages(input.current_properties, ctx, warnings)   # 3
    goal_property_outcomes = build_goal_properties(input.goal_properties, ctx, warnings)     # 4

    goals_internal = build_goals_table(
        retirement, goal_property_outcomes, input.custom_goals, ctx, warnings
    )                                                                           # 5

    horizon = compute_horizon_years(retirement, goals_internal, input.one_off_outflows, ctx)
    monthly_cashflow, annual_cashflow = project_cashflow(                       # 6
        ctx, existing_mortgages,
        [g.amortization for g in goal_property_outcomes if g.amortization],
        input.one_off_inflows, input.one_off_outflows, horizon, warnings
    )

    funding = compute_funding(                                                  # 7
        goals_internal, ctx, monthly_cashflow,
        input.one_off_inflows, input.one_off_outflows, warnings
    )

    headline = build_headline_status(
        ctx, goals_internal, funding, retirement, annual_cashflow, warnings    # 8
    )
    fund_flow = build_fund_flow_summary(
        ctx, annual_cashflow, funding, input.one_off_inflows, input.one_off_outflows
    )

    return GoalPlanningOutput(
        engine_version=ENGINE_VERSION,
        input_echo=input,
        headline=headline,
        retirement=retirement,
        goals=funding.per_goal_status,
        one_off_outflow_status=funding.per_one_off_outflow_status,
        annual_cashflow=annual_cashflow,
        fund_flow_summary=fund_flow,
        monthly_cashflow=monthly_cashflow if input.detail_level == "full" else None,
        nfa_monthly_series=funding.nfa_monthly if input.detail_level == "full" else None,
        mortgage_amortizations=(
            [*existing_mortgages, *(g.amortization for g in goal_property_outcomes if g.amortization)]
            if input.detail_level == "full" else None
        ),
        warnings=warnings,
        computed_at=datetime.utcnow(),
    )
```

**Stage ordering invariant:** each stage depends only on stages above. No back-edges.

### 7.3 Internal types (`_types.py`)

```python
class RunContext(BaseModel):
    # Profile (resolved)
    nfa: float
    latest_update_date: date
    annual_income: float
    annual_household_expense: float                     # = monthly × 12
    monthly_household_expense: float
    monthly_investment_next_12m: float | None
    tax_rate: float

    # Date anchors
    current_fy_end: date
    current_fy_year: int                                # B19 — step-up base year
    near_term_end: date                                 # update + 24 months, FY-end
    medium_term_end: date                               # near + 36 months, FY-end
    horizon_cap_years: int = 80

    # Resolved retirement (filled by .with_retirement())
    retirement_date_considered: date | None = None
    retired_portfolio_roi_annual: float
    real_roi_retired_monthly: float

    # Assumption snapshot
    sip_share: float                                    # B30 default 0.75
    annual_income_growth: float
    annual_invested_amount_growth: float
    inflation_household_expense: float
    near_term_roi: float
    mid_term_roi: float
    long_term_roi: float

    def with_retirement(self, snap: RetirementSnapshot) -> "RunContext":
        return self.model_copy(update={
            "retirement_date_considered": snap.retirement_date,
            "retired_portfolio_roi_annual": snap.real_roi_annual,  # via real-rate calc
            "real_roi_retired_monthly": snap.real_roi_monthly,
        })


class MortgageAnnualRow(BaseModel):
    fy_end: date
    opening_balance: float
    annual_interest: float                              # fractional in first FY
    annual_principal: float                             # = annual_emi_total − annual_interest
    annual_emi_total: float                             # min(emi × months_in_fy, opening + interest)
    closing_balance: float


class MortgageSchedule(BaseModel):
    property_ref: str
    start_date: date
    monthly_rows: list[MortgageAmortizationRow]
    annual_rows: list[MortgageAnnualRow]

    def total_emi_in_fy(self, fy_end: date) -> float: ...
    def total_emi_in_month(self, month_end: date) -> float: ...


class GoalPropertyOutcome(BaseModel):
    name: str
    target_fv: float                                    # full property cost (informational)
    payout_amount_fv: float                             # what's PAID at goal_date (= upfront_FV if mortgage)
    mortgage_amount: float                              # 0 if cash purchase
    amortization: MortgageSchedule | None


class GoalInternal(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    goal_date_fy: date                                  # FY-end of goal_date
    amount_pv: float
    amount_fv: float                                    # for property: payout_amount_fv (upfront-only)
    inflation_rate: float                               # resolved (by goal_type or override)
    expected_roi: float                                 # 3-band horizon lookup
    fund_today_pv: float


class FundingResult(BaseModel):
    nfa_monthly: list[MonthlyNFARow]
    closing_nfa: float                                  # last monthly row's nfa_close
    min_nfa_in_horizon: float
    per_goal_status: list[GoalFundingStatus]            # public type
    per_one_off_outflow_status: list[OneOffFundingStatus]
    per_outflow_underfunded_total: dict[str, float]    # all keys (goals + one-off-out)
    per_outflow_funded_amount: dict[str, float]
```

### 7.4 30 calculations mapped to files

| # | Calc | File | Excel ref |
|---|---|---|---|
| 1 | FY math (current FY end, near-term end +24mo, medium-term end +36mo) | `dates.py`, `profile.py` | B19, B20, B21 |
| 2 | NFA today | `profile.py` | B26 |
| 3 | Annual household expense + FV at retirement | `retirement.py` | B40-B42 |
| 4 | Retirement corpus PV | `retirement.py` | B43 |
| 5 | Existing-mortgage RATE inversion (Newton-Raphson; warn on non-convergence, fall back to 0.075) | `mortgages.py` | B57 |
| 6 | Existing-mortgage monthly amortization with first-FY proration | `mortgages.py` | rows 290+ |
| 7 | Goal-property FV via inflation | `properties.py` | B75 |
| 8 | Goal-property mortgage amount = FV_target − FV_upfront | `properties.py` | B77 |
| 9 | Goal-property EMI via PMT | `properties.py`, `mortgages.py` | B83 |
| 10 | Goal-property amortization with IPMT for interest schedule, fractional first FY | `mortgages.py` | rows 398+ |
| 11 | Unified goals table assembly | `goals_table.py` | rows 92-113 |
| 12 | Per-goal expected ROI (3-band) | `goals_table.py` | col N |
| 13 | Per-goal amount_fv via inflation by goal_type (retirement skips lookup) | `goals_table.py` | col H |
| 14 | Per-goal fund_today_pv | `goals_table.py` | col O |
| 15 | Sum fund_today_pv + present_status | `summary.py` | S105 |
| 16 | Income step-up per FY | `cashflow.py` | col D |
| 17 | Investment step-up per FY | `cashflow.py` | step-up base year B19 |
| 18 | Expense step-up per FY | `cashflow.py` | col F |
| 19 | Income tax = income × tax_rate (flat) | `cashflow.py` | col E |
| 20 | Monthly savings_1 = income − tax − expense | `cashflow.py` | col G |
| 21 | Monthly savings_2 = savings_1 − EMIs | `cashflow.py` | col J |
| 22 | savings_2_avg per FY (FY-bucket average) | `cashflow.py` | col K |
| 23a | Monthly NFA pool evolution | `funding.py` | col N147+ |
| 23b | regular_invest_withdrawal 4-branch rule | `funding.py` | M147 |
| 24 | Per-goal funded_amount = amount_fv − allocated_shortfall | `funding.py` | col M |
| 25 | Funded flag + proportional shortfall split (incl. one-off-out) | `funding.py` | col T, AS-BM |
| 26 | total_funded_amount, closing_nfa, min_nfa_in_horizon | `summary.py` | M113, last NFA |
| 27 | Mini fund-flow bridge | `summary.py` | rows 93-99 |
| 28 | is_overall_feasible (all funded ∧ present_status ≥ 0 ∧ min_nfa ≥ 0) | `summary.py` | composite |
| 29 | total_shortfall_fv distinct from closing_nfa | `summary.py` | L113 vs S214 |
| 30 | Warnings accumulator | all stages | engine-internal |

### 7.5 Engine conventions

**Rounding:** `_round_thousand(x) = round(x / 1000) * 1000`. Applied uniformly to:
- Household expense FV (B42)
- Retirement corpus computed (B43)
- Retirement corpus user FV (B45)
- Goal property FV (B75)
- Goal upfront FV (B76)

Without this, FV cells drift by ±₹500 vs Excel and parity tolerance has to absorb. Engine adopts the convention; output values are explicitly rounded.

**ROI bands:**
- **Per-goal `expected_roi` (3-band):** near (`m ≤ near_term_end`), mid (`near < m ≤ medium_term_end`), long (`m > medium_term_end`).
- **NFA pool ROI in funding (2-band):** near (`m ≤ near_term_end`), long (`m > near_term_end`). Mid-band is unused for the pool — matches Excel exactly.

**`near_term_end` and `medium_term_end` are calendar-month-anchored.** Computed as: `near_term_end = fy_end_after(latest_update_date + 24 months)`; `medium_term_end = fy_end_after(near_term_end + 36 months)`.

**`is_amount_pv` flag** does NOT gate inflation in Excel (B68 only affects upfront fallback). Engine matches: `target_pv` always inflates by `inflation_property`. (`target_fv` short-circuits the inflation step.)

**Excel I147 typo (uses `D147` instead of `C147` in date check)** is NOT replicated. Engine uses correct logic: divisor = months remaining in first FY for partial FY, else 12. Documented as intentional Excel divergence.

**Annual ROI clamp divergence** (Excel annual rows 191+ use `MAX(roi × N, 0)` but monthly rows 147–182 don't): engine uses **monthly tape rolled up**, matches monthly behavior. Small differences vs Excel's annual table when NFA goes negative are acceptable. Documented divergence.

### 7.6 Engine exceptions (`engine/exceptions.py`)

```python
class GoalPlanningEngineError(Exception):
    """Base class for engine errors."""

class MissingDOBError(GoalPlanningEngineError):
    """Date of birth missing — required for retirement calc."""

class PastGoalDateError(GoalPlanningEngineError):
    """Goal date is on or before latest_update_date."""
    # Used per-goal during goals_table assembly; engine drops the goal with warning rather than raising globally

class RATEConvergenceError(GoalPlanningEngineError):
    """RATE inversion did not converge for an existing mortgage."""
    # Caught internally; warning emitted; fallback to assumptions.default_mortgage_interest_annual
```

`MissingDOBError` raises from stage 2a (`compute_retirement_snapshot`).

**Pre-flight vs runtime contract:**
- `validate_input_only(input)` raises `PastGoalDateError` for past goal dates (strict pre-flight check before paying for a projection).
- `compute_full_projection(input)` is more lenient at runtime — past goals are dropped with a warning in `output.warnings`, RATE non-convergence falls back with a warning, etc. The exception classes still exist as types so callers can `except PastGoalDateError` when they wrap `validate_input_only`.

This split lets the bridge layer fail fast on bad input (validator) while the agent gracefully handles partial bad inputs at runtime (engine).

---

## 8. Agent state graph

### 8.1 LangGraph topology (ReAct)

```
START → ingest_baseline → agent → [tool_calls?] → tools → agent → … → END
                                  └── [no tool_calls] → END
```

Standard ReAct loop with `recursion_limit=15`. `GraphRecursionError` → graceful narrative ("I worked through several what-ifs but ran out of room — please ask a more focused question").

### 8.2 AgentState

```python
class CapturedCashflow(BaseModel):
    event: OneOffEvent
    direction: Literal["in", "out"]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    # Refreshed each turn
    baseline_input: GoalPlanningInput
    anchor_date: date

    # Persisted via checkpointer (across turns within session)
    accumulated_overrides: list[OverrideSpec]
    captured_goals: list[CustomGoal]
    captured_properties: list[GoalProperty]
    captured_cashflows: list[CapturedCashflow]
    captured_mutations: list[GoalMutation]              # for mutate_goal calls

    # Computed within turn
    last_output: GoalPlanningOutput | None              # persists across turns for Q&A; invalidated on baseline diff
    last_levers: list[Lever]                            # resets each turn

    # Control
    dirty: bool                                         # gates compute_projection idempotency
    error_log: list[str]
```

### 8.3 Six tools

All tools use LangGraph's `InjectedState` pattern: tools mutate state, return short string summaries to the LLM (not full Pydantic objects).

| # | Tool | Purpose |
|---|---|---|
| 1 | `extract_financial_event(description)` | Returns discriminated union `Union[ExtractedGoal \| ExtractedProperty \| ExtractedCashflow \| ExtractedMutation]`. Routes to the right `captured_*` list. |
| 2 | `apply_override(override)` | Stages a non-goal parameter what-if (rates, income, expense, monthly_investment). Discriminated-union typed. |
| 3 | `clear_overrides(keys=None)` | Clears all or specific override keys. |
| 4 | `mutate_goal(op, goal_name, fields)` | Add/remove/update for any goal (incl. retirement). |
| 5 | `compute_projection()` | Merges baseline + overrides + captures + mutations; calls `compute_full_projection`. **Idempotent**: short-circuits if `not dirty` and `last_output` exists. |
| 6 | `propose_levers()` | Generates up to 7 v1 deterministic levers (A: increase SIP, B: defer goal, C: reduce target, D: change retirement age, E: increase step-up, F: reduce expense, G: pay off existing mortgage early). Each lever pre-validated by re-running engine. Returns top 3 by composite score. Budget: 2.0s for 7 levers × ~250ms each (Lever G skipped if no active existing mortgage). |

### 8.4 Lever generator (7 v1 levers)

| Lever | Action type | Search | Confidence |
|---|---|---|---|
| **A. Increase SIP** | `NumericOverride(monthly_investment_next_12m)` | bisect 1× to `sip_max_multiplier=5×` baseline | high if <2×, medium if <3×, low otherwise |
| **B. Defer goal** | `GoalMutation(op="update", goal_name=X, fields={"goal_date": ...})` | bisect 1–10 yrs (`defer_max_years`); pick smallest deferral that closes gap | high if ≤2y, medium if ≤5y, low otherwise |
| **C. Reduce target** | `GoalMutation(op="update", goal_name=X, fields={"amount_pv": ...})` | bisect 5–50% in 5pp steps | high if ≤15%, medium if ≤30%, low otherwise |
| **D. Change retirement age** | `GoalMutation(op="update", goal_name="retirement", fields={"retirement_age": ...})` | range `[max(baseline+1, current_age+1), assumed_total_age - 5]`, bisect upward | medium |
| **E. Increase step-up rate** | `NumericOverride(step_up_rate)` | bisect from baseline `annual_invested_amount_growth` to `baseline + step_up_max_delta_pp=20pp` | high if Δ ≤ 5pp, medium if ≤ 10pp, low otherwise |
| **F. Reduce household expense** | `NumericOverride(monthly_household_expense)` | try -5%, -10%, -15% reductions | low always (lifestyle change) |
| **G. Pay off existing mortgage early** | `PropertyFieldOverride(property_name=X, field="early_payoff_date", value=...)` | only generated if user has ≥1 active existing mortgage; tries prepayment dates 1y/3y/5y/10y from `latest_update_date`, picks earliest that closes gap | medium |

Every lever asserts both `is_overall_feasible == True` AND `min_nfa_in_horizon >= 0`. A lever that closes shortfall but pushes NFA negative mid-horizon is rejected. Per-call exception handling: if any single engine call fails during lever search, that lever is marked `unavailable`; other levers continue.

**Lever G precondition:** generated only if `state.baseline_input.current_properties` contains at least one property with `has_mortgage=True` AND `mortgage_last_date > latest_update_date` (mortgage is still active). If no active existing mortgage, Lever G is silently skipped.

**Composite ranking** (`levers.py` constants):
```python
CATEGORY_PRIORITY = {
    "A": 1.0,    # SIP increase — most actionable
    "B": 0.9,    # defer goal — soft change
    "E": 0.85,   # step-up — sustainable
    "C": 0.6,    # reduce target — affects life
    "G": 0.55,   # mortgage payoff — requires lump sum
    "D": 0.5,    # delay retirement — affects life
    "F": 0.4,    # cut expense — hardest
}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}
score = (1 / severity_required) × CONFIDENCE_WEIGHT[c] × CATEGORY_PRIORITY[k]
```

The agent receives the **top 3** levers by score (capped via `propose_levers` tool). Beyond 3 just adds noise.

If no lever closes the gap → `Lever(action=GoalMutation(op="remove", goal_name="<largest_underfunded>"), description="Even at maximum levers, this isn't feasible — consider reducing scope", confidence="low")`. Document the convention.

### 8.5 Idempotency, dirty flag, recursion limit

- `dirty=True` set whenever a mutator tool runs (extract, apply_override, clear_overrides, mutate_goal).
- `compute_projection` checks: if `not dirty` and `last_output is not None`, return cached summary string. After running the engine, sets `dirty=False`.
- `propose_levers` no-ops if `last_output is None` (returns "run compute_projection first") or `last_output.headline.is_overall_feasible == True` (returns "no levers needed").
- `recursion_limit=15` on graph invocation. `GraphRecursionError` → fallback narrative.

### 8.6 ingest_baseline_node behavior

```python
def ingest_baseline_node(state: AgentState) -> dict:
    # Validate persisted overrides against fresh baseline; drop orphans
    valid_overrides, dropped_overrides = validate_overrides_against(
        state.get("accumulated_overrides", []), state["baseline_input"]
    )

    # Invalidate last_output if baseline diffs vs last_output.input_echo
    last_out = state.get("last_output")
    invalidate = (
        last_out is not None
        and last_out.input_echo != state["baseline_input"]
    )

    return {
        "accumulated_overrides": valid_overrides,
        "last_levers": [],
        "last_output": None if invalidate else last_out,
        "dirty": bool(dropped_overrides) or invalidate,
        "error_log": [
            *(state.get("error_log", [])),
            *(f"Dropped orphaned override: {o.kind}/{getattr(o, 'key', '')}" for o in dropped_overrides),
        ],
    }
```

### 8.7 Checkpointer

- **Tests:** `MemorySaver` instantiated per-test in a `scope="function"` fixture (avoids state bleed).
- **Production:** `AsyncPostgresSaver` against the existing PG database. `checkpointer.asetup()` called once in FastAPI lifespan (`app/main.py`) on startup. Idempotent; LangGraph manages its own table.
- **No app schema changes** — checkpointer's table is internal to LangGraph.
- **Failure mode:** if PG unavailable on startup, fall back to `MemorySaver` with warning log; periodic health check retries PG.

### 8.8 Public agent API

```python
async def run_cashflow_statement_agent(
    user_message: str,
    baseline_input: GoalPlanningInput,
    chat_session_id: str,                               # caller passes str(turn_context.session_id)
    anchor_date: date,
) -> GoalPlanningResponse:
    config = {
        "configurable": {"thread_id": chat_session_id},
        "recursion_limit": 15,
    }
    state_update = {
        "messages": [HumanMessage(content=user_message)],
        "baseline_input": baseline_input,
        "anchor_date": anchor_date,
    }
    try:
        final_state = await cashflow_statement_graph.ainvoke(state_update, config)
    except GraphRecursionError:
        return GoalPlanningResponse(
            engine_version=ENGINE_VERSION,
            output=None,
            narrative=_RECURSION_LIMIT_MESSAGE,
            levers=[],
        )
    return GoalPlanningResponse(
        engine_version=ENGINE_VERSION,
        output=final_state.get("last_output"),
        narrative=extract_terminal_narrative(final_state["messages"]),
        levers=final_state.get("last_levers", []),
    )
```

`extract_terminal_narrative` walks backward through `messages` to find the last `AIMessage` with empty `tool_calls` (the actual narrative, not a tool-call-triggering message).

**Note:** `conversation_history` is NOT a parameter — checkpointer handles cross-turn history natively via `thread_id`. Bridge does not pass history.

---

## 9. NL extractor + bridge integration

### 9.1 Consolidated extractor

Single chain in `agent/extractor.py`:

```python
EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"
FUZZY_MATCH_THRESHOLD = 85

class FinancialEventExtractor:
    def __init__(self, model: str = EXTRACTOR_MODEL):
        self._llm = ChatAnthropic(model=model, temperature=0)
        self._chain = self._build_chain()

    async def extract(
        self,
        description: str,
        anchor_date: date,
        existing_goal_names: list[str],
    ) -> ExtractedFinancialEvent | ExtractionError:
        try:
            result = await asyncio.to_thread(
                self._chain.invoke,
                {
                    "description": description,
                    "anchor_date": anchor_date.isoformat(),
                    "existing_goal_names": ", ".join(existing_goal_names) or "(none)",
                    "default_property_downpayment_pct": 20,
                    "default_mortgage_tenure_years": 20,
                    "default_mortgage_interest": 0.085,
                },
            )
        except (OutputParserException, ValidationError, anthropic.APIError) as e:
            return ExtractionError(kind="error", reason=f"Could not parse: {e}")

        # Post-fill property defaults (deterministic, not via LLM)
        if result.kind == "property_goal":
            result = self._post_fill_property_defaults(result)

        # Fuzzy collision check → promote to mutation
        if result.kind in ("custom_goal", "property_goal"):
            new_name = result.goal.name if result.kind == "custom_goal" else result.property.name
            best_match = self._best_fuzzy_match(new_name, existing_goal_names)
            if best_match and fuzz.token_set_ratio(_normalize(new_name), _normalize(best_match)) >= FUZZY_MATCH_THRESHOLD:
                return ExtractedMutation(
                    kind="goal_mutation", op="update",
                    goal_name=best_match,
                    fields=self._diff_against_existing(result, best_match),
                )

        # Past-date guard via dated_field accessor
        if (d := result.dated_field()) and d < anchor_date:
            return ExtractionError(kind="error", reason=f"Date {d} is in the past")

        return result
```

**Defaults policy:** property defaults (downpayment 20%, tenure 20y, interest 8.5%) are applied **post-parse, deterministically**. The LLM is asked to use them but the engine guarantees them. `assumptions_used: list[str]` records what was filled in for narrative disclosure.

**Fuzzy match:** `rapidfuzz.fuzz.token_set_ratio` with normalized lowercase + stop-word strip. Threshold 85.

**Past-date check:** `ExtractedFinancialEvent.dated_field()` accessor returns the relevant date attribute per variant (handles `goal.goal_date`, `property.goal_date`, `event.date`, `fields.get("goal_date")`).

**Error path:** `OutputParserException`, `ValidationError`, `anthropic.APIError` all caught; tool returns `"Could not extract: {reason}"` to the LLM. Agent narrates the issue and asks for clarification.

**System prompt** (in `agent/prompts.py`) includes 6 labeled few-shot examples covering: property goal with mortgage, custom goal in PV, custom goal in FV, cashflow inflow, cashflow outflow, goal mutation.

### 9.2 Bridge integration sketch (out of scope for this module)

**`app/services/ai_bridge/goal_planning/input_builder.py`** (verified against actual codebase):

```python
async def build_goal_planning_input(user: User) -> GoalPlanningInput:
    """Map ORM User graph → engine input."""
    return GoalPlanningInput(
        assumptions=Assumptions(),
        profile=ClientProfile(
            latest_update_date=user.personal_finance_profile.latest_update_date or date.today(),
            annual_income=user.personal_finance_profile.annual_income or 0,
            tax_rate=user.tax_profile.income_tax_rate / 100 if user.tax_profile else 0.30,
            financial_assets=user.investment_profile.investable_assets or 0,
            financial_liabilities_excl_mortgage=user.investment_profile.total_liabilities or 0,
            monthly_household_expense=user.investment_profile.regular_outgoings or 0,
            monthly_investment_next_12m=user.personal_finance_profile.monthly_investment_next_12m,
        ),
        retirement=RetirementInput(
            date_of_birth=user.date_of_birth,
            retirement_age=user.retirement_age or 60,
            assumed_total_age=user.assumed_total_age or 85,
            retirement_date_override=user.retirement_date_override,
            retirement_corpus_pv_override=user.retirement_corpus_pv_override,
        ),
        current_properties=[map_orm_to_current(p) for p in user.properties if p.is_current],
        goal_properties=[map_orm_to_goal_property(p) for p in user.properties if not p.is_current],
        custom_goals=[map_financial_goal(g) for g in user.financial_goals if g.goal_type != "HOME_PURCHASE"],
        one_off_inflows=[map_event(e) for e in user.cashflow_events if e.direction == "in"],
        one_off_outflows=[map_event(e) for e in user.cashflow_events if e.direction == "out"],
    )
```

**`app/services/ai_bridge/goal_planning/chat.py`**:

```python
@register("goal_planning")
async def handle_goal_planning(turn_context: TurnContext) -> ChatHandlerResult:
    async with _session_lock(turn_context.session_id):  # asyncio.Lock per session_id (LRU bounded)
        try:
            user = await get_ai_user_context(turn_context.effective_user_id, db=turn_context.db)
            baseline = await build_goal_planning_input(user)
            response = await run_cashflow_statement_agent(
                user_message=turn_context.user_question,
                baseline_input=baseline,
                chat_session_id=str(turn_context.session_id),
                anchor_date=date.today(),
            )

            snapshot_id = None
            if response.output is not None:
                snapshot_id = await record_ai_module_run(
                    db=turn_context.db,
                    user_id=turn_context.effective_user_id,
                    session_id=turn_context.session_id,
                    module="goal_planning",
                    reason="goal_planning_chat_turn",
                    intent_detected="goal_planning",
                    input_payload=response.output.input_echo.model_dump(),
                    output_payload=response.output.model_dump(),
                )

            return ChatHandlerResult(
                text=_format_narrative_with_levers(response.narrative, response.levers),
                snapshot_id=snapshot_id,
            )
        except Exception as e:
            logger.exception("AILAX_AI_MODULE_RUN goal_planning failed: %s", e)
            return ChatHandlerResult(text=_AGENT_DOWN_MESSAGE, snapshot_id=None)
```

`_format_narrative_with_levers` embeds levers as markdown bullets at the tail of `text` (since `ChatHandlerResult` has no chips channel — verified). Structured chips deferred until `ChatHandlerResult` adds the channel.

**`app/services/chat_core/brain.py:210-217` replacement** (one-line):

```python
if intent_value == "goal_planning":
    from app.services.ai_bridge.goal_planning import chat as _gp_chat  # noqa: F401  (late import avoids cycle)
    return await finalize(await dispatch_chat("goal_planning", turn_context))
```

**Persistence** uses the existing `record_ai_module_run` helper (`app/services/ai_module_telemetry.py:21`) — no new helper.

### 9.3 Configuration (`config.py`)

```python
# Module-level constants (no BaseSettings — project pattern)
import os

AGENT_MODEL = os.getenv("GOAL_PLANNING_AGENT_MODEL", "claude-sonnet-4-6")
EXTRACTOR_MODEL = os.getenv("GOAL_PLANNING_EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
RECURSION_LIMIT = int(os.getenv("GOAL_PLANNING_RECURSION_LIMIT", "15"))
USE_CHECKPOINTER = os.getenv("GOAL_PLANNING_USE_CHECKPOINTER", "true").lower() == "true"
CHECKPOINTER_TYPE = os.getenv("GOAL_PLANNING_CHECKPOINTER_TYPE", "postgres")  # "memory" or "postgres"

# Lever search bounds
SIP_MAX_MULTIPLIER = 5.0                # Lever A
DEFER_MAX_YEARS = 10                    # Lever B
REDUCE_MAX_PCT = 0.50                   # Lever C
STEP_UP_MAX_DELTA_PP = 0.20             # Lever E — max additional step-up rate above baseline
EXPENSE_REDUCE_PCT_LIST = (0.05, 0.10, 0.15)  # Lever F
MORTGAGE_PAYOFF_YEARS_LIST = (1, 3, 5, 10)    # Lever G — years from latest_update_date

# Extractor defaults (2026 India)
DEFAULT_PROPERTY_DOWNPAYMENT_PCT = 20.0
DEFAULT_MORTGAGE_TENURE_YEARS = 20
DEFAULT_MORTGAGE_INTEREST_ANNUAL = 0.085
```

API key sourced via `get_anthropic_goal_planning_key()` added to `app/config.py` (mirrors `get_anthropic_asset_allocation_key`).

### 9.4 Logging / observability

- Stdlib `logging.getLogger(__name__)` per module (matches project convention).
- Log markers consistent with existing modules: `AILAX_AI_MODULE_RUN`, `AILAX_CHAT_FLOW`.
- LangSmith **not** wired by default. Enable via `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`. Disabled in production unless explicitly toggled (30+ posts/turn would saturate the LangSmith plan).

### 9.5 Public API surface (`AI_Agents/src/goal_planning/__init__.py`)

```python
from .engine import compute_full_projection, validate_input_only, ENGINE_VERSION
from .agent import cashflow_statement_graph, run_cashflow_statement_agent
from .models import (
    # Inputs
    GoalPlanningInput, Assumptions, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
    # Outputs
    GoalPlanningOutput, GoalPlanningResponse,
    HeadlineStatus, RetirementSnapshot, FundFlowSummary,
    GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow, MonthlyNFARow,
    MortgageAmortization, MortgageAmortizationRow,
    ValidationIssue,
    # Agent types
    OverrideSpec, NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    GoalMutation, LeverAction, Lever,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty,
    ExtractedCashflow, ExtractedMutation, ExtractionError,
    # Enums
    GoalType,
)

__all__ = [
    "compute_full_projection", "validate_input_only", "ENGINE_VERSION",
    "cashflow_statement_graph", "run_cashflow_statement_agent",
    "GoalPlanningInput", "GoalPlanningOutput", "GoalPlanningResponse",
    "Assumptions", "ClientProfile", "RetirementInput",
    "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent",
    "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
    "GoalFundingStatus", "OneOffFundingStatus",
    "AnnualCashflowRow", "MonthlyCashflowRow", "MonthlyNFARow",
    "MortgageAmortization", "MortgageAmortizationRow", "ValidationIssue",
    "OverrideSpec", "NumericOverride", "RateOverride",
    "PerGoalRateOverride", "PropertyFieldOverride",
    "GoalMutation", "LeverAction", "Lever",
    "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
    "ExtractedCashflow", "ExtractedMutation", "ExtractionError",
    "GoalType",
]
```

**Boundary rule:** bridge code imports only from `goal_planning` (top-level). Internal types (`RunContext`, `MortgageSchedule`, `GoalInternal`, `MortgageAnnualRow`, `GoalPropertyOutcome`, `FundingResult`, exception classes) live in `engine/_types.py` / `engine/exceptions.py` — not exported.

---

## 10. Testing strategy

### 10.1 Test pyramid

| Layer | Count | Tooling |
|---|---|---|
| Unit (engine submodules) | ~90 | pytest, hypothesis (≥3 property tests) |
| Integration (engine pipeline + parity) | ~20 | pytest |
| Excel parity scenarios | 1 + 3 (#01 + 02–04) | pytest (committed JSON fixtures) |
| Synthetic parity | 13 closed-form cases | pytest + numpy_financial |
| Agent E2E | ~30 (tools, levers, e2e) | FakeChatAnthropic + MemorySaver per test |
| Boundary lint | 2 | ast |
| Performance | 2 (latency, memory) | time.perf_counter, tracemalloc |

### 10.2 Excel parity harness

**`scripts/extract_excel_reference.py` (DEV-ONLY)** — reads the Excel, evaluates formulas via LibreOffice headless (using `scripts/recalc.py` from xlsx skill), writes per-scenario `input.json` + `expected.json`. Scenarios authored before v1 ships:

| # | Scenario | Source |
|---|---|---|
| 01 | Sourabh's baseline as-is | existing Excel |
| 02 | `no_mortgages` | author variant Excel |
| 03 | `already_retired` | author variant Excel |
| 04 | `overfunded` | author variant Excel |

**Pre-extracted JSON committed to git** under `tests/fixtures/excel_reference/<scenario>/`. CI does NOT run LibreOffice. A weekly cron CI job re-runs extraction as a sanity check.

**Cell mapping** (full table in `tests/fixtures/excel_reference/cell_mapping.md` and `scripts/extract_excel_reference.py`). Key entries:

| Excel | Output field |
|---|---|
| B26 | `headline.net_financial_assets_today` |
| B43 | `retirement.corpus_required_computed` |
| B44 | `retirement.corpus_required_user_override` |
| B46 | `retirement.corpus_required_used` |
| B86 | `headline.number_of_goals` |
| B88 | `headline.last_fy_end_date` |
| O113 | `headline.sum_fund_today_pv` |
| L113 | `headline.total_shortfall_fv` (sign-flipped from Excel's negative) |
| M113 | `headline.total_funded_amount` |
| S105 | `headline.present_status` |
| (last NFA) | `headline.closing_nfa` (computed from `monthly_cashflow[-1].nfa_close`) |
| H93..H112 | per-goal `amount_fv` |
| L93..L112 | per-goal `shortfall_fv` (sign-flipped) |
| M93..M112 | per-goal `funded_amount` |
| AS290..BM290 | per-outflow `per_outflow_underfunded_total[name]` |
| H190..H289 | annual `existing_mortgage_emi_total` per FY |

**Tolerance:** type-aware.
- `bool` / `int` / enum (e.g., `regular_invest_kind`) → exact equality.
- `float` → default `rel_tol=0.005, abs_tol=100`.
- Per-cell overrides:
  - L113, M113 (very large totals): `rel_tol=0.001`
  - AS290..BM290 (small per-outflow values): `abs_tol=10`
  - Cells where `|expected| < 1000`: `abs_tol=50`

**`MonthlyNFARow` spot-check:** 5 representative rows per scenario (FY1-M1, FY1-M12, retirement-month, last-drawdown-month, terminal-month) — not all ~600 monthly rows.

**`FundFlowSummary` bridge identity** as a separate test (`test_excel_bridge_identity`): asserts `closing == opening + invest + roi + one_off_in − one_off_out − goals` for every FY.

**Failure UX:**
```
AssertionError: Excel parity mismatch [scenario=baseline]
  cell:     L113
  formula:  =SUM(AS113:BM113)
  expected: 12,500,000.00 (rel_tol=0.005, abs_tol=100)
  actual:   12,562,500.00
  diff:     +62,500.00 (+0.50%)
```

### 10.3 Synthetic parity (13 cases)

Each test hand-computes the expected value via `numpy_financial` (or simple arithmetic) and asserts engine matches with `rel_tol=0.001`.

| # | Case | Closed-form |
|---|---|---|
| 1 | Single retirement goal corpus | `-npf.pv(real_roi_annual, post_retire_yrs, annual_expense_FV)` |
| 2 | Cash-purchase property payout FV | `target_pv × (1+inflation_property)^years_to_goal` |
| 3 | Mortgaged property EMI | `npf.pmt(monthly_rate, total_months, -mortgage_amount)` |
| 4 | Empty goals NFA growth (2-band) | `nfa × (1+near_roi)^near_yrs × (1+long_roi)^(N-near_yrs)` |
| 5 | Existing-mortgage RATE inversion round-trip | `npf.pmt(inferred_rate, months, -balance) ≈ given_emi` |
| 6 | Goal funded with huge NFA | `is_funded=True`, `shortfall_fv == 0` |
| 7a | Two equal goals same date, NFA half-coverage | shortfalls proportional (≈ equal) |
| 7b | Three goals, total need 3× NFA | per-goal underfunded by 2/3 of FV (proportional) |
| 7c | Mixed goals + one_off_out same month | shortfall split incl. one_off_out |
| 8 | Per-goal expected_roi 3-band lookup | goal in <2y → near_roi; 2-5y → mid_roi; >5y → long_roi |
| 9 | Already-retired person | drawdown from t=0; engine doesn't divide by zero |
| 10 | Past-date goal rejected | engine drops with warning, continues; no error |
| 12 | Goal-property mortgage IPMT year 2+ | `interest[y] = npf.ipmt(rate, y+1, n, -principal)` matches engine |
| 13 | M147 4-branch coverage | one input per branch; assert `regular_invest_kind` matches each |
| 14 | Step-up compounding across FY boundary | `FY3_income = FY1_income × (1+growth)^2` |

(Test #11 multi-existing-mortgage roll-up deferred to v2.)

### 10.4 Agent E2E with `FakeChatAnthropic`

```python
class FakeChatAnthropic(ChatAnthropic):
    """Returns canned AIMessage responses without HTTP. Sidesteps SDK retry semantics."""
    def __init__(self, responses: list[AIMessage], **kwargs):
        self._responses = iter(responses)
    def invoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)
    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)
```

Six E2E tests:
1. Initial query → `compute_projection` → narrate
2. What-if "retire at 58" → `apply_override` (NumericOverride doesn't allow retirement_age post-Q3, so this becomes `mutate_goal`) → `compute_projection` → narrate
3. NL goal capture "send daughter abroad in 2040" → `extract_financial_event` → `compute_projection` → narrate
4. Q&A "explain my retirement" with cached `last_output` → no tool calls, narrate from cache
5. Shortfall scenario → `compute_projection` → `propose_levers` → narrate with markdown levers
6. Recursion limit hit → graceful narrative

`pytest-recording` (VCR) used for ONE wire-format smoke test against real API; cassettes committed under `tests/fixtures/llm_mocks/cassettes/`.

`MemorySaver` instantiated per test in a `scope="function"` fixture.

### 10.5 Boundary lint tests

**`tests/boundary/test_engine_no_llm.py`** — engine has zero LLM imports (NO `anthropic` exception carve-out, stricter than project rule):

```python
FORBIDDEN = ("langchain_anthropic", "anthropic", "langchain_core", "langgraph")

def test_engine_has_no_llm_imports():
    violations = []
    for py_file in ENGINE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in FORBIDDEN):
                        violations.append(f"{py_file}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in FORBIDDEN):
                    violations.append(f"{py_file}:{node.lineno} from {node.module}")
    assert not violations, "Engine has LLM imports:\n" + "\n".join(violations)
```

**`tests/boundary/test_public_api.py`** — bridge imports only top-level `goal_planning`. Walks both `ast.Import` and `ast.ImportFrom`. Forbidden prefixes: `goal_planning.engine`, `goal_planning.agent`, plus full-path variants (`AI_Agents.src.goal_planning.engine`, `AI_Agents.src.goal_planning.agent`).

### 10.6 Coverage gates

```yaml
- run: pytest --cov=AI_Agents/src/goal_planning/engine --cov-fail-under=95 \
                --cov-report=term-missing:skip-covered \
                --cov-report=html
- run: pytest --cov=AI_Agents/src/goal_planning/agent --cov-fail-under=80
```

Engine 95%, agent 80%. Boundary tests pass/fail (no threshold). `htmlcov/` gitignored.

### 10.7 Performance / memory

**`tests/integration/test_engine_performance.py`** (NOT marked slow — perf regressions fail fast):

```python
def test_engine_call_under_500ms(realistic_input):
    start = time.perf_counter()
    output = compute_full_projection(realistic_input)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"Engine too slow: {elapsed*1000:.0f}ms"

def test_engine_memory_under_50mb(realistic_input):
    tracemalloc.start()
    output = compute_full_projection(realistic_input)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 50 * 1024 * 1024, f"Engine peak memory: {peak/1024/1024:.1f}MB"
```

`realistic_input` uses Indian-realistic numbers: annual_income ∈ [15L, 30L], NFA ∈ [50L, 5Cr], goal amounts [50L, 5Cr], 50-year horizon, 21 goals.

Lever search budget: 2.0s (7 levers × ~250ms each; Lever G skipped if no active existing mortgage, typically ~1.5s in practice). Tested in `tests/agent/test_levers.py` with explicit timer assertion.

### 10.8 Test infrastructure

**`requirements-dev.txt`** (NEW — doesn't currently exist):
```
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=5.0
pytest-recording>=0.13
pytest-rerunfailures>=14.0
respx>=0.21
time-machine>=2.14
hypothesis>=6.100
numpy-financial>=1.0.0
rapidfuzz>=3.0
```

**`pyproject.toml`** (NEW `[tool.pytest.ini_options]`):
```toml
[tool.pytest.ini_options]
pythonpath = ["AI_Agents/src", "."]
asyncio_mode = "auto"
markers = [
    "real_llm: requires ENABLE_LLM_SMOKE=1; not run in CI",
    "slow: long-running tests",
    "excel_parity: requires LibreOffice or pre-extracted fixtures",
    "performance: latency/memory regression checks",
]
```

CI invocation: `pytest -m "not excel_parity and not slow and not real_llm"` for PR; nightly runs all.

---

## 11. Edge cases and error handling

| Case | Behavior |
|---|---|
| DOB missing | `MissingDOBError` from stage 2a; engine entry contract says raise on unrecoverable inputs |
| Already-retired (`retirement_date_considered ≤ latest_update_date`) | Drawdown branch from t=0; warning emitted; engine continues |
| Goal date ≤ `latest_update_date` | Drop with warning; engine continues without that goal |
| `monthly_investment_next_12m is None` vs `0` | Distinct: `None` triggers fallback (`savings_2_avg × sip_share`); `0` forces zero invest |
| RATE non-convergence on existing mortgage | Warn, fall back to `assumptions.default_mortgage_interest_annual = 0.075` |
| Goal-mortgage outliving retirement | Warn; EMIs continue post-retirement via cashflow chain |
| `annual_income == 0` | Allowed silently; savings goes negative; M147 fallback handles withdrawal |
| `sum_fund_today_pv == 0` (no goals or all already funded) | Headline returns with `closing_nfa` only; no per-goal records |
| Property goal name collides with current property name | `_validate_unique_names` raises `ValueError` at input boundary |
| Engine raises during `compute_projection` tool call | Tool catches, returns error string `"ERROR: <msg>"` to LLM; agent narrates |
| Anthropic API down at `agent_node` | Bridge layer wraps `dispatch_chat` in try/except → returns `_AGENT_DOWN_MESSAGE` |
| Postgres unavailable on startup | Fall back to `MemorySaver` with warning; periodic health check retries PG |
| Concurrent turns same `session_id` | Bridge layer asyncio.Lock per session_id (LRU bounded) serializes |
| LLM emits invalid override key (LLM mistake) | Pydantic discriminated-union rejects; tool returns error string |
| NL extractor produces past-date goal | `ExtractionError(reason="Date X is in the past")`; tool returns error string |
| `clear_overrides([])` (empty list) | No-op; returns "No overrides to clear" |
| `mutate_goal(op="remove", goal_name="retirement")` | **Allowed per Q3** — engine treats retirement as a goal; removal means "no retirement planning"; engine still produces `RetirementSnapshot` with default 60/85 if removed but DOB present (warn) |
| Lever G generated when user has no active existing mortgage | **Skipped silently** — Lever G enumerator returns empty list; other 6 levers proceed |
| Lever G with `early_payoff_date` before `latest_update_date` | Engine rejects via `PropertyFieldOverride` validation; lever marked unavailable, others continue |
| Lever G with `early_payoff_date` after `mortgage_last_date` (mortgage already paid off naturally) | Engine treats as no-op (mortgage already done); lever marked unavailable |
| Lever F producing negative `monthly_household_expense` | Engine rejects (validation: expense must be ≥ 0); lever marked unavailable |
| Lever E with `step_up_rate` exceeding `STEP_UP_MAX_DELTA_PP` | Lever rejects without engine call (search bound); returns "step-up alone insufficient" |

---

## 12. Implementation phasing (input to writing-plans)

Each phase has a verifiable goal. Phase N+1 cannot start until Phase N verifies.

### Phase 1: Engine + parity
- Implement `financial_primitives/` + `goal_planning/engine/` (13 files) + `models.py`
- Author Excel scenarios 02–04 + extract reference JSON
- Implement 13 synthetic parity tests
- Implement boundary lint tests
- Implement performance + memory tests
- **Verify:** Excel parity scenarios 01–04 pass; 13 synthetic tests pass; engine call <500ms; memory <50MB; 95% engine coverage; lint tests pass

### Phase 2: Agent + 7 levers
- Implement `goal_planning/agent/` (8 files): state, graph, nodes, tools, levers, prompts
- Implement all 7 lever generators (A–G); Lever G conditionally skipped when no active existing mortgage
- Implement `FakeChatAnthropic` test harness
- Implement 6 agent E2E tests with mocked LLM
- Implement lever-specific unit tests: A (bisect convergence), B (defer cap), C (reduce cap), D (retirement-age range), E (step-up bisect), F (expense reduction), G (only with existing mortgage), composite ranking, top-3 truncation, mid-horizon NFA rejection
- **Verify:** all 6 E2E tests pass; lever generator produces up to 7 levers (top 3 returned) within 2.0s budget; 80% agent coverage; recursion-limit fallback works

### Phase 3: NL extractor
- Implement `agent/extractor.py` with consolidated discriminated-union extractor
- 6 few-shot examples
- Past-date guard, fuzzy match
- 4-kind round-trip tests
- **Verify:** extractor returns correct kind for each of 4 categories on hand-crafted inputs; collision detection promotes to mutation correctly; past-date returns ExtractionError

### Phase 4: Public API polish
- Implement top-level `__init__.py` with `__all__`
- Implement `config.py` (module constants only)
- Document all public types in docstrings
- **Verify:** boundary lint test passes for `goal_planning.engine` and `goal_planning.agent`; sample bridge code (in tests) imports only public API

### v2 (separate spec, not this doc)
- Synthetic test #11 (multi-mortgage roll-up)
- Excel parity scenarios 05–10
- Vectorized mortgage amortization (perf optimization)
- Agent ability to toggle `detail_level`
- LangSmith tracing on by default
- Bridge layer (separate integration spec)
- DB schema additions (separate migration spec)

---

## 13. Open questions / known divergences

| # | Topic | Decision / status |
|---|---|---|
| 1 | Excel I147 typo (uses D147 not C147 for date) → divisor always 12 | **Engine doesn't replicate.** Uses correct months_in_first_fy. Documented divergence. |
| 2 | Excel annual ROI clamp `MAX(roi × N, 0)` (rows 191+) but monthly rows don't clamp | **Engine uses monthly tape rolled up.** Small divergence vs Excel annual table when NFA goes negative; acceptable for v1. |
| 3 | Retirement skips inflation lookup (Excel I93 uses name-based VLOOKUP that returns 0) | **Engine special-cases retirement** in `goals_table.py`; matches Excel behavior. |
| 4 | `ROUND(_, -3)` Excel convention | **Engine adopts** via `_round_thousand()` helper applied uniformly to FV outputs. |
| 5 | Goal-property `is_amount_pv` flag (Excel B68 doesn't gate inflation) | **Engine matches Excel** — `target_pv` always inflates; `target_fv` short-circuits. Flag not exposed in our contract; we use `target_pv \| target_fv` instead. |
| 6 | `_xlfn.SINGLE`, `_xlfn.IFNA` Excel namespace | **LibreOffice support varies.** Extraction script falls back to Python recompute for unsupported formulas. Document fallback table. |
| 7 | Bridge layer schema additions | **Out of scope for this spec.** Concrete column list in Appendix B; integration spec to follow. |
| 8 | `goal_subtype` metadata on `financial_goals` for `_abroad` vs `_local` distinction (Q5) | **Bridge concern.** Engine accepts the distinct subtypes; bridge populates from the new column. |
| 9 | Excel scenarios 02–04 require Sourabh to author variant Excels | **Pre-v1 dependency.** Cannot ship v1 without them. |

---

## Appendix A: Excel cell mapping (full)

(Truncated in this doc; full table maintained in `scripts/extract_excel_reference.py` docstring and `tests/fixtures/excel_reference/cell_mapping.md`. Updated alongside Excel changes.)

Key mappings already enumerated in §10.2.

---

## Appendix B: Schema additions for integration phase (deferred)

When the bridge integration phase begins, the following schema work is required. **None of this is in v1 of this module.**

### New tables

```sql
CREATE TABLE cashflow_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    amount NUMERIC(15, 2) NOT NULL,
    date DATE NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'one_time' CHECK (frequency IN ('one_time', 'monthly', 'yearly')),
    end_date DATE,
    inflation_rate NUMERIC(5, 4),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    value NUMERIC(15, 2) NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    purchase_date DATE,
    mortgage_balance NUMERIC(15, 2),
    mortgage_interest NUMERIC(5, 4),
    mortgage_emi NUMERIC(15, 2),
    mortgage_end_date DATE,
    mortgage_balance_as_of_date DATE,
    UNIQUE (user_id, name)
);
```

### Field additions

- `users.latest_update_date DATE`
- `users.assumed_total_age INT`
- `users.retirement_date_override DATE`
- `users.retirement_corpus_pv_override NUMERIC(15, 2)`
- `users.monthly_investment_next_12m NUMERIC(15, 2)`
- `financial_goals.goal_subtype TEXT` (for `child_education_abroad` vs `_local` distinction; values: `"abroad"`, `"local"`, NULL)

### Bridge mapping

| Engine field | ORM source |
|---|---|
| `ClientProfile.tax_rate` | `tax_profile.income_tax_rate / 100` |
| `ClientProfile.financial_assets` | `investment_profile.investable_assets` |
| `ClientProfile.financial_liabilities_excl_mortgage` | `investment_profile.total_liabilities` |
| `ClientProfile.monthly_household_expense` | `investment_profile.regular_outgoings` |
| `RetirementInput.date_of_birth` | `users.date_of_birth` |
| `current_properties` | `properties WHERE is_current = TRUE` |
| `goal_properties` | `properties WHERE is_current = FALSE` |
| `custom_goals` | `financial_goals WHERE goal_type NOT IN ('HOME_PURCHASE', 'VEHICLE')` |
| `one_off_inflows` / `_outflows` | `cashflow_events WHERE direction = ...` |

### ORM `goals.enums.GoalType` → engine `GoalType` map

| ORM | Engine |
|---|---|
| `RETIREMENT` | `retirement` |
| `CHILD_EDUCATION` + `goal_subtype="abroad"` | `child_abroad_education` |
| `CHILD_EDUCATION` + `goal_subtype="local"` (or NULL) | `child_local_education` |
| `WEDDING` | `child_marriage` |
| `HOME_PURCHASE`, `VEHICLE` | (route to `goal_properties`, not `custom_goals`) |
| `OTHER`, `WEALTH_CREATION`, `EMERGENCY_FUND`, `TRAVEL` | `custom` |

---

## Appendix C: Approval log

This spec was developed through 6 sections of incremental brainstorming with audit passes between sections. Lock points:

- Section 1 (package layout): locked
- Section 2 (Pydantic contracts): locked at v2 with cross-section patches S2-1 through S2-7
- Section 3 (engine subdivision): locked at v3 with cross-section patches S3-1 through S3-6
- Section 4 (agent state graph): locked at v2 with cross-section patches S4-1 through S4-6, plus Q3 (mutate_goal handles retirement)
- Section 5 (NL extractor + integration): locked at v2 with audit fixes A1-A7, B1-B7, C1-C2, D1-D2, E1
- Section 6 (testing strategy): locked at v2 with audit fixes B-J

Three audit passes by independent agents (Sections 2, 3, 4, 5, 6) caught:
- Funding model architecture flip (per-goal balance evolution → shared NFA pool with proportional shortfall)
- M147 4-branch rule for `regular_invest_withdrawal`
- Goal-property payout = upfront FV (not target FV)
- First-FY proration for mortgages
- Several wrong claims about codebase shape (no `BaseSettings`, no `structlog`, no `AiUserContext`, no `chips` channel on `ChatHandlerResult`)
- Tool surface gaps (`mutate_goal`, `clear_overrides`)
- LangGraph checkpointer pattern (drop "rebuild from messages")
- Test infrastructure missing (`requirements-dev.txt`, `pyproject.toml` central config don't exist in repo)

User-approved scope decisions:
- Q1: Excel scenarios 02–04 authored before v1 ships
- Q2: Discriminated-union `Lever.action`
- Q3: `mutate_goal` works on retirement
- Q4: `last_output` invalidates on baseline diff
- Q5: `goal_subtype` metadata column for child education abroad vs local

Post-spec amendment (2026-05-09):
- Levers E (step-up rate), F (expense reduction), G (mortgage payoff) **pulled from v2 into v1** — full 7-lever surface in initial release. Updated `§8.4`, `§9.3` config constants, `§10.7` lever budget (1.5s → 2.0s), `§11` edge cases (5 new entries for Lever G/F/E preconditions), `§12` Phase 2 verification criteria. `PropertyFieldOverride.field` literal extended with `"early_payoff_date"` for Lever G against existing properties.
