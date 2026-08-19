"""The field registry — the ONE place that maps a profile field to its home.

Every detailed-onboarding field the chat may ask about is declared here once:
the question in PI's voice, how it is answered, how it is validated, and which
table/column owns it. Chat, the completeness service and the write router all
read this; nothing else in the chat path names a profile table.

Deliberately ORM-free and import-free (stdlib only) so it can be imported from
the AI layer, the gate and the routers without dragging models along. Reading
and writing the declared columns is the completeness service's and the write
router's job respectively — they dispatch on ``FieldSpec.table``.

Adding a field is one entry here plus, if its table is new to the registry, one
handler in ``profile_write_router`` and one reader in
``profile_completeness_service``.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Literal

InputKind = Literal["money", "percent", "integer", "date", "enum", "text"]

# The canonical unit a value is STORED in. The extractor is told this and must
# convert into it (a customer who answers "2.4 lakh a month" to an
# ``inr_per_year`` field returns 2880000).
Unit = Literal["inr", "inr_per_year", "inr_per_month", "percent", "years", "none"]

Section = Literal["money_map", "goals", "risk_behaviour", "tax_details", "personal"]


@dataclass(frozen=True)
class FieldSpec:
    """One capturable profile field."""

    key: str
    question: str
    input_kind: InputKind
    table: str
    column: str
    section: Section
    unit: Unit = "none"
    # Allowed answers for ``enum`` fields, verbatim as stored. These are the
    # SAME strings /profile/complete writes — chat and the form must not
    # produce two vocabularies for one column.
    options: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    # Lower sorts first when the gate has to pick one field to ask about.
    priority: int = 100
    # Extra steer for the extractor when the phrasing is ambiguous.
    hint: str | None = None
    # True when changing this field must trigger an effective-risk recalculation.
    risk_input: bool = False

    @property
    def is_enum(self) -> bool:
        return self.input_kind == "enum"


# ---------------------------------------------------------------------------
# Option vocabularies — kept identical to CompleteProfile.tsx.
# ---------------------------------------------------------------------------

HORIZON_OPTIONS: tuple[str, ...] = ("< 2 years", "2–5 years", "5+ years")

EXPERIENCE_OPTIONS: tuple[str, ...] = (
    "I am a novice. I am new to investing and financial markets.",
    "I have a basic understanding of investing. I understand basic investment "
    "concepts like diversification and risks.",
    "I am enthusiastic about investing. I understand how markets fluctuate and "
    "the pros and cons of different investment classes.",
    "I am an experienced investor. I have invested in different markets and "
    "understand different investment strategies. I have developed my own "
    "investment philosophy.",
)

FOCUS_OPTIONS: tuple[str, ...] = (
    "Keep it safe — I'll accept low returns to protect my money",
    "Mostly steady — small dips are fine for modest growth",
    "Balanced — I'll ride moderate ups and downs for moderate growth",
    "Growth-first — I can handle big swings for higher long-term returns",
    "Maximise growth — I'm comfortable with large losses while chasing the "
    "highest returns",
)

DROP_REACTION_OPTIONS: tuple[str, ...] = (
    "Capital preservation is paramount. Cut losses immediately and liquidate "
    "all investments.",
    "Transfer investments to safer asset classes to prevent further loss.",
    "Would feel worried but would wait to give your investments a little more "
    "time.",
    "Accept volatility and dips in portfolio value as part of investing. Will "
    "keep investments as they are.",
    "Buy the dip to bring the average buying price lower. Comfortable sitting "
    "with lower portfolio values and waiting for the market to recover in the "
    "long term.",
)

TAX_REGIME_OPTIONS: tuple[str, ...] = ("old", "new")

# Matches EMERGENCY_TIMEFRAMES in CompleteProfile.tsx, minus its "Custom"
# entry — that is a form affordance that opens a free-text box, not a value the
# column ever stores.
EMERGENCY_FUND_MONTHS_OPTIONS: tuple[str, ...] = (
    "3 months",
    "6 months",
    "12 months",
)


# ---------------------------------------------------------------------------
# The registry.
# ---------------------------------------------------------------------------

_FIELDS: tuple[FieldSpec, ...] = (
    # --- money map -------------------------------------------------------
    FieldSpec(
        key="annual_income",
        question="Roughly what do you earn in a year, before tax?",
        input_kind="money",
        unit="inr_per_year",
        table="personal_finance_profiles",
        column="annual_income",
        section="money_map",
        min_value=0,
        max_value=10_000_000_000,
        priority=10,
        hint=(
            "Store the ANNUAL figure. If the customer answers per month, "
            "multiply by 12. If it is unclear whether they meant per month or "
            "per year, ask instead of assuming."
        ),
        risk_input=True,
    ),
    FieldSpec(
        key="monthly_household_expense",
        question="And what does a typical month cost your household?",
        input_kind="money",
        unit="inr_per_month",
        table="personal_finance_profiles",
        column="monthly_household_expense",
        section="money_map",
        min_value=0,
        max_value=1_000_000_000,
        priority=20,
        hint=(
            "Store the MONTHLY figure. If the customer answers per year, "
            "divide by 12."
        ),
        risk_input=True,
    ),
    FieldSpec(
        key="financial_assets",
        question=(
            "What do you have set aside today in cash, deposits and debt "
            "investments?"
        ),
        input_kind="money",
        unit="inr",
        table="personal_finance_profiles",
        column="financial_assets",
        section="money_map",
        min_value=0,
        max_value=100_000_000_000,
        priority=30,
        hint="Cash, savings, fixed deposits and debt funds. Not equities, not property.",
        risk_input=True,
    ),
    FieldSpec(
        key="equity_shares",
        question="Do you hold any direct equity shares, and roughly what are they worth?",
        input_kind="money",
        unit="inr",
        table="personal_finance_profiles",
        column="equity_shares",
        section="money_map",
        min_value=0,
        max_value=100_000_000_000,
        priority=70,
    ),
    FieldSpec(
        key="financial_liabilities_excl_mortgage",
        question="Do you have any loans outstanding, apart from a home loan?",
        input_kind="money",
        unit="inr",
        table="personal_finance_profiles",
        column="financial_liabilities_excl_mortgage",
        section="money_map",
        min_value=0,
        max_value=100_000_000_000,
        priority=75,
        risk_input=True,
    ),
    FieldSpec(
        key="starting_monthly_investment",
        question="How much are you investing each month right now?",
        input_kind="money",
        unit="inr_per_month",
        table="personal_finance_profiles",
        column="starting_monthly_investment",
        section="money_map",
        min_value=0,
        max_value=1_000_000_000,
        priority=40,
    ),
    # --- goals -----------------------------------------------------------
    FieldSpec(
        key="investment_horizon",
        question="How long would you like this money to stay invested?",
        input_kind="enum",
        options=HORIZON_OPTIONS,
        table="personal_finance_profiles",
        column="investment_horizon",
        section="goals",
        priority=35,
        risk_input=True,
    ),
    FieldSpec(
        key="retirement_age",
        question="At what age would you like to stop working?",
        input_kind="integer",
        unit="years",
        table="investment_profiles",
        column="retirement_age",
        section="goals",
        min_value=35,
        max_value=90,
        priority=45,
    ),
    FieldSpec(
        key="target_corpus",
        question="Is there a number you're aiming to get to?",
        input_kind="money",
        unit="inr",
        table="investment_profiles",
        column="target_corpus",
        section="goals",
        min_value=0,
        max_value=1_000_000_000_000,
        priority=80,
    ),
    FieldSpec(
        key="emergency_fund_months",
        question="How many months of expenses do you keep as an emergency buffer?",
        input_kind="enum",
        options=EMERGENCY_FUND_MONTHS_OPTIONS,
        table="investment_profiles",
        column="emergency_fund_months",
        section="goals",
        priority=85,
    ),
    # --- risk / behaviour -------------------------------------------------
    FieldSpec(
        key="investment_experience",
        question="How would you describe your investing experience so far?",
        input_kind="enum",
        options=EXPERIENCE_OPTIONS,
        table="risk_profiles",
        column="investment_experience",
        section="risk_behaviour",
        priority=50,
        hint="Pick the option closest to what they describe; never invent wording.",
        risk_input=True,
    ),
    FieldSpec(
        key="investment_focus",
        question="When you invest, what matters more to you — growth, or protecting what you have?",
        input_kind="enum",
        options=FOCUS_OPTIONS,
        table="risk_profiles",
        column="investment_focus",
        section="risk_behaviour",
        priority=52,
        risk_input=True,
    ),
    FieldSpec(
        key="drop_reaction",
        question=(
            "If your investments fell by about 20% over a year, what would you do?"
        ),
        input_kind="enum",
        options=DROP_REACTION_OPTIONS,
        table="risk_profiles",
        column="drop_reaction",
        section="risk_behaviour",
        priority=54,
        risk_input=True,
    ),
    # --- tax --------------------------------------------------------------
    FieldSpec(
        key="income_tax_rate",
        question="Which income tax slab do you fall in?",
        input_kind="percent",
        unit="percent",
        table="tax_profiles",
        column="income_tax_rate",
        section="tax_details",
        min_value=0,
        max_value=45,
        priority=60,
        hint=(
            "The marginal slab rate as a percentage, 0 to 45. '30% bracket' → 30. "
            "Store the number, not a fraction."
        ),
    ),
    FieldSpec(
        key="tax_regime",
        question="Are you on the old tax regime or the new one?",
        input_kind="enum",
        options=TAX_REGIME_OPTIONS,
        table="tax_profiles",
        column="tax_regime",
        section="tax_details",
        priority=65,
    ),
    # --- personal ---------------------------------------------------------
    FieldSpec(
        key="date_of_birth",
        question="What's your date of birth?",
        input_kind="date",
        table="users",
        column="date_of_birth",
        section="personal",
        priority=15,
        hint=(
            "ISO format YYYY-MM-DD. Indian customers usually say DD/MM/YYYY — "
            "read 03/04/1985 as 3 April 1985."
        ),
        risk_input=True,
    ),
    FieldSpec(
        key="occupation",
        question="What do you do for a living?",
        input_kind="text",
        table="users",
        column="occupation",
        section="personal",
        priority=90,
        risk_input=True,
    ),
    FieldSpec(
        key="family_status",
        question="Who depends on you financially?",
        input_kind="text",
        table="users",
        column="family_status",
        section="personal",
        priority=95,
        risk_input=True,
    ),
)

FIELD_REGISTRY: dict[str, FieldSpec] = {f.key: f for f in _FIELDS}

# Tables the registry knows how to touch. The write router and the completeness
# service each assert against this so a new table cannot be added in one place
# and forgotten in the other.
KNOWN_TABLES: frozenset[str] = frozenset(f.table for f in _FIELDS)


# ---------------------------------------------------------------------------
# Per-intent requirements — what the gate consults.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentRequirement:
    """What an intent needs before its engine can honestly run.

    ``hard`` fields block: the engine cannot compute without them, so the turn
    is spent asking. ``soft`` fields improve the answer but never delay it —
    at most one is appended as a closing question.
    """

    hard: tuple[str, ...] = ()
    soft: tuple[str, ...] = ()
    # Only for the Retirement case: hard only when the condition holds.
    conditional_hard: tuple[str, ...] = dc_field(default_factory=tuple)


REQUIREMENTS: dict[str, IntentRequirement] = {
    # NOTE the key: this is NOT the intent name. ``financial_planning`` covers
    # both CREATING a goal — which needs a cost and a date and nothing else —
    # and TESTING the plan, which does need their income. Only the second
    # requires these, so ``flow_financial_planning`` applies this row at the
    # point where it is about to run the projection, and describing a wedding
    # never triggers a question about salary.
    "financial_planning_projection": IntentRequirement(
        hard=(
            "annual_income",
            "monthly_household_expense",
            "financial_assets",
            "investment_horizon",
        ),
        soft=("starting_monthly_investment", "target_corpus"),
        conditional_hard=("retirement_age",),
    ),
    "asset_allocation": IntentRequirement(
        hard=(
            "investment_experience",
            "investment_focus",
            "drop_reaction",
            "date_of_birth",
        ),
        soft=("income_tax_rate", "emergency_fund_months", "investment_horizon"),
    ),
    "rebalancing": IntentRequirement(
        hard=(
            "investment_experience",
            "investment_focus",
            "drop_reaction",
            "income_tax_rate",
        ),
        soft=("tax_regime", "date_of_birth"),
    ),
    "additional_investment": IntentRequirement(
        hard=("investment_horizon",),
        soft=("starting_monthly_investment", "annual_income"),
    ),
    # The intent itself requires nothing: a customer stating a figure, naming a
    # goal or asking what we hold is answerable with an empty record. The
    # projection is the only part with prerequisites, and it carries its own row
    # above.
    "financial_planning": IntentRequirement(),
}

# Intents that are answerable without any profile at all. Listed explicitly
# rather than inferred from REQUIREMENTS so that forgetting to add a
# requirement row can never silently start gating a read-only question.
NEVER_GATED_INTENTS: frozenset[str] = frozenset(
    {
        "portfolio_query",
        "mutual_fund_query",
        "general_chat",
        "general_market_query",
        "out_of_scope",
        "stock_advice",
    }
)


def spec(key: str) -> FieldSpec | None:
    return FIELD_REGISTRY.get(key)


def specs_for(keys: tuple[str, ...] | list[str]) -> list[FieldSpec]:
    """Registry entries for ``keys``, in ask-priority order, skipping unknowns."""
    found = [FIELD_REGISTRY[k] for k in keys if k in FIELD_REGISTRY]
    return sorted(found, key=lambda f: (f.priority, f.key))


def requirement_for(intent: str) -> IntentRequirement:
    return REQUIREMENTS.get(intent, IntentRequirement())


__all__ = [
    "FIELD_REGISTRY",
    "FieldSpec",
    "IntentRequirement",
    "KNOWN_TABLES",
    "NEVER_GATED_INTENTS",
    "REQUIREMENTS",
    "requirement_for",
    "spec",
    "specs_for",
]
