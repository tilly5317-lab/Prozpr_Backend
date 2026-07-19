"""Build a ``cashflow_statement.GoalPlanningInput`` from a User ORM row.

Financial scalars come from ``personal_finance_profiles`` only
(``app.domains.profile.services.profile_finance``). Owned properties come from
``investment_profiles.current_properties``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.cashflow.services.goal_planning_engine.cashflow_trace import (
    log_inputs,
)
from app.domains.cashflow.services.goal_planning_engine.readiness import (
    evaluate_cashflow_readiness,
    retirement_age_from_goals,
)
from app.domains.profile.services.profile_finance import (
    current_portfolio_corpus_pfp,
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


# Standard planned retirement age used when the user hasn't supplied one.
# Mirrors the assumed_lifespan_years=100 default — a planning assumption, not a
# zero-fill, so it does not violate the "real numbers only" readiness gate.
DEFAULT_RETIREMENT_AGE = 60

_ORM_GOAL_TYPE_TO_ENGINE: dict[str, GoalType] = {
    "CHILD_EDUCATION": GoalType.child_local_education,
    "HOME_PURCHASE": GoalType.property,
}


def _map_custom_goals(
    financial_goals: List[Any],
    today: date,
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
            issues.append(
                f"goal:{goal_name} skipped — duplicate name in your goals list."
            )
            continue
        seen_names.add(norm)
        # Retirement is NO LONGER injected from the profile — it is considered
        # only when the user adds it as a goal. So a goal named "Retirement" (or
        # type RETIREMENT) now flows through as a normal custom goal rather than
        # being skipped. (See model_retirement=False below.)

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
                inflation_override = (
                    float(infl) / 100.0 if float(infl) > 1 else float(infl)
                )
            except (TypeError, ValueError):
                pass
        if engine_type == GoalType.property:
            issues.append(
                f"goal:{goal_name} (HOME_PURCHASE) modeled as a cash goal — "
                "downpayment and mortgage data are not yet captured on the profile"
            )
        mapped.append(
            CustomGoal(
                name=goal_name,
                goal_type=engine_type,
                goal_value_pv=pv,
                goal_date=target,
                inflation_rate_override=inflation_override,
            )
        )
    return mapped, issues


def _map_current_properties(user: Any) -> list[CurrentProperty]:
    return [
        CurrentProperty(
            name=p.name,
            property_value=float(p.property_value)
            if p.property_value is not None
            else None,
            has_mortgage=bool(p.has_mortgage),
            mortgage_emi=float(p.mortgage_emi) if p.mortgage_emi is not None else None,
            mortgage_end_date=p.mortgage_end_date,
        )
        for p in current_properties_for_user(user)
    ]


def build_goal_planning_input_for_user(
    user: Any,
    anchor_date: date,
    *,
    portfolio_value: float | None = None,
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

    # The readiness gate above guarantees the required finance scalars (income,
    # expense, DOB) are present — so no missing-profile/zero-fill fallbacks fire
    # for those. Real values only for the figures that can't be assumed.
    scalars = personal_finance_scalars(user)

    # Retirement age. Base = the stored investment-profile value, defaulting to
    # the standard 60 (a planning assumption mirroring assumed_lifespan_years=100
    # below). A Retirement GOAL (goal_type RETIREMENT / name "retire…") can only
    # EXTEND it — the goal is the user's statement of retiring later, and the
    # horizon must reach it — never shorten it: the projection always runs to
    # max(retirement_age default 60, last goal), and a retirement goal earlier
    # than that is just a goal inside the horizon (see cashflow_statement
    # pipeline, which ends at max(retirement_date, last_goal_date)).
    goal_retirement_age = retirement_age_from_goals(user, anchor_date)
    raw_retirement_age = getattr(inv, "retirement_age", None) if inv is not None else None
    if raw_retirement_age is not None:
        base_retirement_age = int(raw_retirement_age)
    else:
        base_retirement_age = DEFAULT_RETIREMENT_AGE
        defaults_applied.append(f"retirement_age={DEFAULT_RETIREMENT_AGE}")
    if goal_retirement_age is not None and goal_retirement_age > base_retirement_age:
        retirement_age = goal_retirement_age
        defaults_applied.append(
            f"retirement_age={goal_retirement_age} (extended by Retirement goal)"
        )
    else:
        retirement_age = base_retirement_age
    target_corpus_today = getattr(inv, "target_corpus", None) if inv else None
    retirement_override = float(target_corpus_today) if target_corpus_today else None

    custom_goals, goal_issues = _map_custom_goals(financial_goals, anchor_date)
    validation_issues.extend(goal_issues)

    current_properties = _map_current_properties(user)
    if not current_properties:
        defaults_applied.append("current_properties=[]")

    # Lifespan is no longer collected from the user — everyone is planned to
    # age 100. Honour an explicitly-set value if one exists, else assume 100.
    assumed_lifespan_years = int(getattr(user, "assumed_lifespan_years", None) or 100)
    defaults_applied.extend(
        [
            "goal_properties=[]",
            "one_off_inflows=[]",
            "one_off_outflows=[]",
        ]
    )

    # Starting corpus = liquid cash & assets + the user's current portfolio value.
    # Prefer the live portfolio value (single source of truth — the portfolio /
    # CAMS data); fall back to a manually-entered corpus only when no portfolio is
    # linked yet. This represents the total current assets the user holds.
    manual_corpus = current_portfolio_corpus_pfp(pfp)
    live_corpus = (
        float(portfolio_value) if portfolio_value and portfolio_value > 0 else 0.0
    )
    portfolio_corpus = live_corpus if live_corpus > 0 else manual_corpus
    defaults_applied.append(
        f"portfolio_corpus={'live' if live_corpus > 0 else 'manual'}:{portfolio_corpus:.0f}"
    )

    # Opening corpus = cash & debt (financial_assets) + equities (equity_shares)
    # + the current MF portfolio. "Other assets" (gold, FDs, unlisted shares…)
    # are deliberately EXCLUDED — they are not treated as part of the investable
    # opening corpus the projection grows. The two manual figures are edited
    # separately on onboarding / goal-planning but roll up into one corpus, so the
    # projection and the displayed inputs stay in sync.
    equity_shares_total = scalars["equity_shares"] or 0.0
    if equity_shares_total > 0:
        defaults_applied.append(f"equity_shares={equity_shares_total:.0f}")

    profile = ClientProfile(
        annual_income=scalars["annual_income"],
        effective_tax_rate=effective_tax_rate_for_user(user),
        financial_assets=scalars["financial_assets"]
        + equity_shares_total
        + portfolio_corpus,
        financial_liabilities_excl_mortgage=scalars[
            "financial_liabilities_excl_mortgage"
        ],
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
        # Retirement is NOT auto-modelled: no injected retirement corpus drawdown
        # and income is not stopped at retirement age. Retirement counts as a goal
        # only if the user adds it explicitly (it then flows through `custom_goals`
        # above). RetirementInput is still passed (required by the schema, populates
        # the output's retirement view, and — regardless of this flag — drives the
        # projection horizon, which always runs to max(last_goal, retirement_age)).
        model_retirement=False,
    )

    debug: Dict[str, Any] = {
        "has_personal_finance_profile": pfp is not None,
        "has_investment_profile": inv is not None,
        "current_property_count": len(current_properties),
        "active_goal_count": len(custom_goals),
        "defaults_applied": defaults_applied,
        "validation_issues": validation_issues,
    }

    # Single chokepoint for both the chat and REST run paths — log the exact
    # inputs the engine is about to run on (retirement age/lifespan come from the
    # user here, never assumed; the readiness gate above guarantees it).
    log_inputs(user_id=getattr(user, "id", None), gp_input=inp, debug=debug)

    return inp, debug
