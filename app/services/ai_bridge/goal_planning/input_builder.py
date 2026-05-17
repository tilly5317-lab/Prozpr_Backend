"""Build a ``cashflow_statement.GoalPlanningInput`` from a User ORM row.

ORM coverage gaps (deliberately defaulted; see CLAUDE.md / planning notes):
- ``retirement.assumed_lifespan_years``: not stored; engine default (85) is used.
- ``current_properties``: only scalar aggregates exist on InvestmentProfile;
  no per-property mortgage list. Defaulted to ``[]``.
- ``goal_properties``: ``FinancialGoal.goal_type=HOME_PURCHASE`` rows are
  mapped to the engine's ``property`` ``custom_goals`` (no downpayment /
  mortgage math). Defaulted to ``[]``.
- ``one_off_inflows`` / ``one_off_outflows``: only scalar placeholders;
  defaulted to ``[]``.

Each gap that fires is recorded in ``debug["defaults_applied"]`` so the
caller (and the formatter LLM) can surface the limitation to the user.

Entry: ``build_goal_planning_input_for_user(user, anchor_date) -> (GoalPlanningInput, debug)``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from cashflow_statement import (
    Assumptions, ClientProfile, CustomGoal, GoalPlanningInput,
    GoalType, RetirementInput,
)


_DEFAULT_TAX_RATE = 0.25  # blended; mirrors asset_allocation default

# ORM `goal_type_enum` → engine GoalType. Only the obvious ones are routed to
# specific engine types; everything else collapses to ``custom`` so the engine
# applies a generic inflation rate. HOME_PURCHASE is mapped to ``property``
# (so the engine uses the property inflation rate) but stays in ``custom_goals``
# because we don't have downpayment/mortgage data — see module docstring.
_ORM_GOAL_TYPE_TO_ENGINE: dict[str, GoalType] = {
    "CHILD_EDUCATION": GoalType.child_local_education,
    "HOME_PURCHASE": GoalType.property,
}


def _f(obj: Any, attr: str, default: float = 0.0) -> float:
    return float(getattr(obj, attr, None) or default)


def _map_custom_goals(
    financial_goals: List[Any], today: date,
) -> tuple[List[CustomGoal], List[str]]:
    """Map ACTIVE, future-dated FinancialGoal rows to engine CustomGoals.

    RETIREMENT goals are skipped — the engine builds retirement from
    ``RetirementInput`` separately. Returns the mapped list plus any
    per-row issues (skipped rows, defaults applied).
    """
    issues: List[str] = []
    mapped: List[CustomGoal] = []
    for g in financial_goals:
        status_val = getattr(g, "status", None)
        status_name = (
            status_val.value if hasattr(status_val, "value") else str(status_val or "")
        )
        if status_name.upper() != "ACTIVE":
            continue
        target = getattr(g, "target_date", None)
        if not target or target <= today:
            continue

        gt = getattr(g, "goal_type", None)
        gt_name = (gt.value if hasattr(gt, "value") else str(gt or "")).upper()
        if gt_name == "RETIREMENT":
            continue  # retirement handled by RetirementInput

        engine_type = _ORM_GOAL_TYPE_TO_ENGINE.get(gt_name, GoalType.custom)
        goal_name = getattr(g, "goal_name", None) or "goal"
        pv = float(getattr(g, "present_value_amount", 0.0) or 0.0)
        inflation_override = None
        infl = getattr(g, "inflation_rate", None)
        if infl is not None:
            try:
                inflation_override = float(infl) / 100.0
            except (TypeError, ValueError):
                pass
        if engine_type == GoalType.property:
            issues.append(
                f"goal:{goal_name} (HOME_PURCHASE) modeled as a cash goal — "
                "downpayment and mortgage data are not yet captured on the profile"
            )
        mapped.append(CustomGoal(
            name=goal_name,
            goal_type=engine_type,
            goal_value_pv=pv,
            goal_date=target,
            inflation_rate_override=inflation_override,
        ))
    return mapped, issues


def build_goal_planning_input_for_user(
    user: Any, anchor_date: date,
) -> tuple[GoalPlanningInput, Dict[str, Any]]:
    """Map a User ORM (with eager-loaded profile + goals) to ``GoalPlanningInput``.

    Raises ``ValueError("missing_date_of_birth")`` when DOB is absent — same
    contract as ``asset_allocation.input_builder``.
    """
    if getattr(user, "date_of_birth", None) is None:
        raise ValueError("missing_date_of_birth")

    inv = getattr(user, "investment_profile", None)
    tp = getattr(user, "tax_profile", None)
    financial_goals = list(getattr(user, "financial_goals", []) or [])

    defaults_applied: List[str] = []
    validation_issues: List[str] = []

    if inv is None:
        defaults_applied.append("investment_profile_missing")
    if tp is None or getattr(tp, "income_tax_rate", None) is None:
        defaults_applied.append("tax_profile_missing")
        effective_tax_rate = _DEFAULT_TAX_RATE
        validation_issues.append(
            f"Using default tax rate ({int(_DEFAULT_TAX_RATE * 100)}%) — "
            "complete your tax profile for an accurate projection."
        )
    else:
        # TaxProfile stores 0–100; engine wants 0.0–1.0.
        effective_tax_rate = float(tp.income_tax_rate) / 100.0

    annual_income = _f(inv, "annual_income")
    monthly_household_expense = _f(inv, "regular_outgoings")
    financial_assets = _f(inv, "investable_assets")
    financial_liabilities = _f(inv, "total_liabilities")
    starting_monthly_investment = _f(inv, "monthly_savings") or None

    retirement_age = int(getattr(inv, "retirement_age", None) or 60)
    target_corpus_today = getattr(inv, "target_corpus", None)
    retirement_override = (
        float(target_corpus_today) if target_corpus_today else None
    )

    custom_goals, goal_issues = _map_custom_goals(financial_goals, anchor_date)
    validation_issues.extend(goal_issues)

    # Coverage gaps that always fire (until ORM extensions land).
    defaults_applied.extend([
        "assumed_lifespan_years=85_default",
        "current_properties=[]",
        "goal_properties=[]",
        "one_off_inflows=[]",
        "one_off_outflows=[]",
    ])

    profile = ClientProfile(
        annual_income=max(annual_income, 0.0),
        effective_tax_rate=max(0.0, min(1.0, effective_tax_rate)),
        financial_assets=max(financial_assets, 0.0),
        financial_liabilities_excl_mortgage=max(financial_liabilities, 0.0),
        monthly_household_expense=max(monthly_household_expense, 0.0),
        starting_monthly_investment=starting_monthly_investment,
    )
    retirement = RetirementInput(
        date_of_birth=user.date_of_birth,
        retirement_age=retirement_age,
        retirement_corpus_pv_today_override=retirement_override,
    )

    inp = GoalPlanningInput(
        assumptions=Assumptions(),
        profile=profile,
        retirement=retirement,
        current_properties=[],
        goal_properties=[],
        custom_goals=custom_goals,
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="default",
    )

    debug: Dict[str, Any] = {
        "has_investment_profile": inv is not None,
        "has_tax_profile": tp is not None,
        "active_goal_count": len(custom_goals),
        "defaults_applied": defaults_applied,
        "validation_issues": validation_issues,
    }
    return inp, debug
