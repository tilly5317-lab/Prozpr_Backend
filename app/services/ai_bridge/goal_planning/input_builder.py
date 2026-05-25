"""Input builder — maps ORM User graph to ``GoalPlanningInput``.

Handles:
- ``cashflow_input_assumptions``: if absent for the user, seeds a row with
  engine defaults and uses those.
- ``personal_finance_profile``: maps to ``ClientProfile``.
- ``financial_goals``: maps retirement, property, and custom goal rows into
  their respective engine DTOs.
- ``current_properties``: maps to ``CurrentProperty``.
- ``cashflow_one_off_events``: maps to ``OneOffEvent``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cashflow.assumption import CashflowInputAssumption
from app.models.cashflow.one_off_event import CashflowInputOneOffEvent
from app.models.goals.financial_goal import FinancialGoal
from app.models.profile.personal_finance_profile import PersonalFinanceProfile
from app.models.profile.user_current_property import UserCurrentProperty
from app.models.user import User

logger = logging.getLogger(__name__)

_DEFAULT_ASSUMPTIONS = {
    "general_inflation": 0.06,
    "inflation_property": 0.06,
    "inflation_child_abroad_education": 0.08,
    "inflation_child_local_education": 0.06,
    "inflation_child_marriage": 0.06,
    "inflation_household_expense": 0.06,
    "inflation_post_retirement": 0.06,
    "annual_income_growth": 0.08,
    "annual_invested_amount_growth": 0.08,
    "roi_near_term_post_tax": 0.05,
    "roi_mid_term_post_tax": 0.07,
    "roi_long_term_post_tax": 0.09,
    "roi_retired_portfolio_annual": 0.09,
    "near_term_horizon_years": 2,
    "medium_term_horizon_years": 3,
    "default_mortgage_tenure_years": 20,
    "default_mortgage_interest_annual": 0.075,
    "default_property_downpayment_pct": 0.20,
    "default_sip_share": 0.75,
}


def _to_float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


async def _ensure_assumptions(
    db: AsyncSession, user_id: uuid.UUID,
) -> CashflowInputAssumption:
    """Return the user's assumption row, creating one with defaults if absent."""
    stmt = select(CashflowInputAssumption).where(
        CashflowInputAssumption.user_id == user_id
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    row = CashflowInputAssumption(user_id=user_id, **_DEFAULT_ASSUMPTIONS)
    db.add(row)
    await db.flush()
    logger.info("Seeded default cashflow_input_assumptions for user_id=%s", user_id)
    return row


def _build_assumptions_dict(row: CashflowInputAssumption) -> dict[str, Any]:
    """Convert ORM assumption row to kwargs for ``Assumptions(...)``."""
    return {
        "inflation_property": _to_float(row.inflation_property),
        "inflation_child_abroad_education": _to_float(row.inflation_child_abroad_education),
        "inflation_child_local_education": _to_float(row.inflation_child_local_education),
        "inflation_child_marriage": _to_float(row.inflation_child_marriage),
        "inflation_household_expense": _to_float(row.inflation_household_expense),
        "inflation_post_retirement": _to_float(row.inflation_post_retirement),
        "annual_income_growth": _to_float(row.annual_income_growth),
        "annual_invested_amount_growth": _to_float(row.annual_invested_amount_growth),
        "roi_near_term_post_tax": _to_float(row.roi_near_term_post_tax),
        "roi_mid_term_post_tax": _to_float(row.roi_mid_term_post_tax),
        "roi_long_term_post_tax": _to_float(row.roi_long_term_post_tax),
        "roi_retired_portfolio_annual": _to_float(row.roi_retired_portfolio_annual),
        "near_term_horizon_years": int(row.near_term_horizon_years),
        "medium_term_horizon_years": int(row.medium_term_horizon_years),
        "default_mortgage_tenure_years": int(row.default_mortgage_tenure_years),
        "default_mortgage_interest_annual": _to_float(row.default_mortgage_interest_annual),
        "default_property_downpayment_pct": _to_float(row.default_property_downpayment_pct),
        "default_sip_share": _to_float(row.default_sip_share),
    }


def _build_client_profile(pfp: PersonalFinanceProfile | None) -> dict[str, Any] | None:
    """Map ``PersonalFinanceProfile`` ORM to ``ClientProfile`` kwargs.

    Returns None if the essential fields are missing (caller must handle).
    """
    if pfp is None:
        return None
    annual_income = _to_float(pfp.annual_income)
    if annual_income <= 0:
        return None
    return {
        "annual_income": annual_income,
        "effective_tax_rate": _to_float(pfp.effective_tax_rate) or 0.15,
        "financial_assets": _to_float(pfp.financial_assets),
        "financial_liabilities_excl_mortgage": _to_float(pfp.financial_liabilities_excl_mortgage),
        "monthly_household_expense": _to_float(pfp.monthly_household_expense),
        "starting_monthly_investment": (
            _to_float(pfp.starting_monthly_investment)
            if pfp.starting_monthly_investment is not None
            else None
        ),
    }


def _build_retirement_input(
    goals: list[FinancialGoal], user: User,
) -> dict[str, Any] | None:
    """Extract retirement goal from goals table or fallback to user DOB."""
    retirement_goal = next(
        (g for g in goals if g.goal_type_cashflow and g.goal_type_cashflow.value == "retirement"),
        None,
    )

    dob = None
    if retirement_goal and retirement_goal.date_of_birth:
        dob = retirement_goal.date_of_birth
    elif user.date_of_birth:
        dob = user.date_of_birth

    if dob is None:
        return None

    result: dict[str, Any] = {"date_of_birth": dob}

    assumed_lifespan = user.assumed_lifespan_years or 85
    result["assumed_lifespan_years"] = assumed_lifespan

    if retirement_goal and retirement_goal.goal_date:
        result["retirement_date_override"] = retirement_goal.goal_date
    else:
        result["retirement_age"] = 60

    return result


def _build_goal_properties(goals: list[FinancialGoal]) -> list[dict[str, Any]]:
    """Extract property-type goals into GoalProperty kwargs."""
    props = []
    for g in goals:
        if not g.goal_type_cashflow or g.goal_type_cashflow.value != "property":
            continue
        if not g.goal_date:
            continue
        entry: dict[str, Any] = {
            "name": g.name or g.goal_name or "Property",
            "goal_date": g.goal_date,
            "is_downpayment_only": bool(g.is_downpayment_only),
        }
        if g.target_pv is not None:
            entry["target_pv"] = _to_float(g.target_pv)
        if g.target_fv is not None:
            entry["target_fv"] = _to_float(g.target_fv)
        if entry.get("target_pv") is None and entry.get("target_fv") is None:
            continue
        if g.upfront_amount is not None:
            entry["upfront_amount"] = _to_float(g.upfront_amount)
        if g.downpayment_pct is not None:
            entry["downpayment_pct"] = _to_float(g.downpayment_pct)
        if g.inflation_annual is not None:
            entry["inflation_annual"] = _to_float(g.inflation_annual)
        if g.mortgage_tenure_years is not None:
            entry["mortgage_tenure_years"] = int(g.mortgage_tenure_years)
        if g.mortgage_interest_annual is not None:
            entry["mortgage_interest_annual"] = _to_float(g.mortgage_interest_annual)
        if g.is_downpayment_only:
            if entry.get("upfront_amount") is None and entry.get("downpayment_pct") is None:
                entry["downpayment_pct"] = 0.20
        props.append(entry)
    return props


def _build_custom_goals(goals: list[FinancialGoal]) -> list[dict[str, Any]]:
    """Extract non-retirement, non-property goals into CustomGoal kwargs."""
    custom_types = {
        "child_abroad_education", "child_local_education",
        "child_marriage", "custom",
    }
    customs = []
    for g in goals:
        if not g.goal_type_cashflow or g.goal_type_cashflow.value not in custom_types:
            continue
        if not g.goal_date:
            continue
        pv = _to_float(g.goal_value_pv) if g.goal_value_pv is not None else None
        fv = _to_float(g.goal_value_fv) if g.goal_value_fv is not None else None
        if pv is None and fv is None:
            if g.present_value_amount:
                pv = _to_float(g.present_value_amount)
            else:
                continue
        entry: dict[str, Any] = {
            "name": g.name or g.goal_name or "Goal",
            "goal_type": g.goal_type_cashflow.value,
            "goal_date": g.goal_date,
        }
        if pv is not None:
            entry["goal_value_pv"] = pv
        if fv is not None:
            entry["goal_value_fv"] = fv
        if g.inflation_annual is not None:
            entry["inflation_rate_override"] = _to_float(g.inflation_annual)
        customs.append(entry)
    return customs


def _build_current_properties(properties: list[UserCurrentProperty]) -> list[dict[str, Any]]:
    """Map UserCurrentProperty ORM rows to CurrentProperty kwargs."""
    result = []
    for p in properties:
        entry: dict[str, Any] = {
            "name": p.name,
            "has_mortgage": bool(p.has_mortgage),
        }
        if p.property_value is not None:
            entry["property_value"] = _to_float(p.property_value)
        if p.has_mortgage:
            entry["mortgage_emi"] = _to_float(p.mortgage_emi)
            entry["mortgage_end_date"] = p.mortgage_end_date
        result.append(entry)
    return result


def _build_one_off_events(
    events: list[CashflowInputOneOffEvent],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split one-off events into inflows and outflows."""
    inflows: list[dict[str, Any]] = []
    outflows: list[dict[str, Any]] = []
    for e in events:
        entry = {
            "description": e.description,
            "amount": _to_float(e.amount),
            "date": e.event_date,
        }
        if e.direction and e.direction.value == "in":
            inflows.append(entry)
        else:
            outflows.append(entry)
    return inflows, outflows


async def build_goal_planning_input_for_user(
    user: User,
    db: AsyncSession,
    anchor_date: date | None = None,
) -> dict[str, Any]:
    """Build the full ``GoalPlanningInput`` kwargs dict from the ORM User graph.

    Raises ``ValueError("missing_date_of_birth")`` if retirement cannot be computed.
    Raises ``ValueError("missing_financial_profile")`` if the ClientProfile cannot be built.

    Returns a dict ready for ``GoalPlanningInput(**result)``.
    """
    user_id = user.id

    assumption_row = await _ensure_assumptions(db, user_id)
    assumptions_kwargs = _build_assumptions_dict(assumption_row)

    pfp: PersonalFinanceProfile | None = user.personal_finance_profile
    profile_kwargs = _build_client_profile(pfp)
    if profile_kwargs is None:
        raise ValueError("missing_financial_profile")

    goals: list[FinancialGoal] = list(user.financial_goals) if user.financial_goals else []

    retirement_kwargs = _build_retirement_input(goals, user)
    if retirement_kwargs is None:
        raise ValueError("missing_date_of_birth")

    goal_properties = _build_goal_properties(goals)
    custom_goals = _build_custom_goals(goals)

    properties: list[UserCurrentProperty] = (
        list(user.current_properties) if user.current_properties else []
    )
    current_properties_kwargs = _build_current_properties(properties)

    one_off_events: list[CashflowInputOneOffEvent] = (
        list(user.cashflow_one_off_events) if user.cashflow_one_off_events else []
    )
    inflows, outflows = _build_one_off_events(one_off_events)

    return {
        "assumptions": assumptions_kwargs,
        "profile": profile_kwargs,
        "retirement": retirement_kwargs,
        "current_properties": current_properties_kwargs,
        "goal_properties": goal_properties,
        "custom_goals": custom_goals,
        "one_off_inflows": inflows,
        "one_off_outflows": outflows,
    }
