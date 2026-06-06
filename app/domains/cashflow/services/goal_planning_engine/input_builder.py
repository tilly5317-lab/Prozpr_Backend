"""Build a ``cashflow_statement.GoalPlanningInput`` from a User ORM row.

Financial scalars come from ``personal_finance_profiles`` only
(``app.domains.profile.services.profile_finance``). Owned properties come from
``investment_profiles.current_properties``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.cashflow.services.goal_planning_engine.readiness import (
    evaluate_cashflow_readiness,
)
from app.domains.profile.services.profile_finance import (
    current_properties_for_user,
    effective_tax_rate_for_user,
    personal_finance_scalars,
)

ensure_ai_agents_path()

from cashflow_statement import (
    Assumptions,
    ClientProfile,
    CurrentProperty,
    CustomGoal,
    GoalPlanningInput,
    GoalType,
    RetirementInput,
)


_ORM_GOAL_TYPE_TO_ENGINE: dict[str, GoalType] = {
    "CHILD_EDUCATION": GoalType.child_local_education,
    "HOME_PURCHASE": GoalType.property,
}

# GoalPlanningInput reserves this name for RetirementInput (see engine validator).
_ENGINE_RESERVED_GOAL_NAMES = frozenset({"retirement"})


def _is_engine_retirement_goal(goal_name: str, goal_type_name: str) -> bool:
    """True when the row duplicates engine retirement (profile + RetirementInput)."""
    if goal_type_name.upper() == "RETIREMENT":
        return True
    return goal_name.casefold() in _ENGINE_RESERVED_GOAL_NAMES


def _map_custom_goals(
    financial_goals: List[Any], today: date,
) -> tuple[List[CustomGoal], List[str]]:
    issues: List[str] = []
    mapped: List[CustomGoal] = []
    seen_names: set[str] = set()
    for g in financial_goals:
        status_val = getattr(g, "status", None)
        status_name = (
            status_val.value if hasattr(status_val, "value") else str(status_val or "")
        )
        if status_name.upper() != "ACTIVE":
            continue
        target = getattr(g, "target_date", None) or getattr(g, "goal_date", None)
        if not target or target <= today:
            continue

        gt = getattr(g, "goal_type", None)
        gt_name = (gt.value if hasattr(gt, "value") else str(gt or "")).upper()
        goal_name = getattr(g, "name", None) or getattr(g, "goal_name", None) or "goal"
        norm = goal_name.casefold()
        if norm in seen_names:
            issues.append(f"goal:{goal_name} skipped — duplicate name in your goals list.")
            continue
        seen_names.add(norm)
        if _is_engine_retirement_goal(goal_name, gt_name):
            issues.append(
                f"goal:{goal_name} skipped — retirement is modeled from your profile "
                "(date of birth, retirement age, target corpus), not as a custom goal."
            )
            continue

        engine_type = _ORM_GOAL_TYPE_TO_ENGINE.get(gt_name, GoalType.custom)
        pv = float(
            getattr(g, "goal_value_pv", None)
            or getattr(g, "present_value_amount", None)
            or 0.0
        )
        inflation_override = None
        infl = getattr(g, "inflation_rate", None)
        if infl is not None:
            try:
                inflation_override = float(infl) / 100.0 if float(infl) > 1 else float(infl)
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


def _map_current_properties(user: Any) -> list[CurrentProperty]:
    return [
        CurrentProperty(
            name=p.name,
            property_value=float(p.property_value) if p.property_value is not None else None,
            has_mortgage=bool(p.has_mortgage),
            mortgage_emi=float(p.mortgage_emi) if p.mortgage_emi is not None else None,
            mortgage_end_date=p.mortgage_end_date,
        )
        for p in current_properties_for_user(user)
    ]


def build_goal_planning_input_for_user(
    user: Any, anchor_date: date,
) -> tuple[GoalPlanningInput, Dict[str, Any]]:
    if getattr(user, "date_of_birth", None) is None:
        raise ValueError("missing_date_of_birth")

    # Gate: the engine must run on the user's real numbers, never zero-filled or
    # defaulted placeholders. Refuse until every required input is supplied.
    readiness = evaluate_cashflow_readiness(user)
    blocking = [k for k in readiness["missing"] if k != "date_of_birth"]
    if blocking:
        raise ValueError("missing_required_inputs:" + ",".join(blocking))

    pfp = getattr(user, "personal_finance_profile", None)
    inv = getattr(user, "investment_profile", None)
    financial_goals = list(getattr(user, "financial_goals", []) or [])

    defaults_applied: List[str] = []
    validation_issues: List[str] = []

    # The readiness gate above guarantees pfp, inv, retirement_age, the finance
    # scalars and a tax rate are all present — so no missing-profile/default-tax
    # fallbacks can fire here. Real values only.
    scalars = personal_finance_scalars(user)
    retirement_age = int(inv.retirement_age)
    target_corpus_today = getattr(inv, "target_corpus", None) if inv else None
    retirement_override = float(target_corpus_today) if target_corpus_today else None

    custom_goals, goal_issues = _map_custom_goals(financial_goals, anchor_date)
    validation_issues.extend(goal_issues)

    current_properties = _map_current_properties(user)
    if not current_properties:
        defaults_applied.append("current_properties=[]")

    assumed_lifespan_years = int(user.assumed_lifespan_years)
    defaults_applied.extend([
        "goal_properties=[]",
        "one_off_inflows=[]",
        "one_off_outflows=[]",
    ])

    profile = ClientProfile(
        annual_income=scalars["annual_income"],
        effective_tax_rate=effective_tax_rate_for_user(user),
        financial_assets=scalars["financial_assets"],
        financial_liabilities_excl_mortgage=scalars["financial_liabilities_excl_mortgage"],
        monthly_household_expense=scalars["monthly_household_expense"],
        starting_monthly_investment=scalars["starting_monthly_investment"],
    )
    retirement = RetirementInput(
        date_of_birth=user.date_of_birth,
        retirement_age=retirement_age,
        assumed_lifespan_years=assumed_lifespan_years,
        retirement_corpus_pv_today_override=retirement_override,
    )

    inp = GoalPlanningInput(
        assumptions=Assumptions(),
        profile=profile,
        retirement=retirement,
        current_properties=current_properties,
        goal_properties=[],
        custom_goals=custom_goals,
        one_off_inflows=[],
        one_off_outflows=[],
        detail_level="default",
    )

    debug: Dict[str, Any] = {
        "has_personal_finance_profile": pfp is not None,
        "has_investment_profile": inv is not None,
        "current_property_count": len(current_properties),
        "active_goal_count": len(custom_goals),
        "defaults_applied": defaults_applied,
        "validation_issues": validation_issues,
    }
    return inp, debug
