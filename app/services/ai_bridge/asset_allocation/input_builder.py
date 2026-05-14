"""Build the allocation engine input from the ORM User graph.

Reads date_of_birth, investment_profile, financial_goals, portfolios, and
effective_risk_assessments to assemble the DTO the engine expects.
Falls back gracefully when the engine package is not installed.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def _age_from_dob(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _effective_risk_score(user: Any) -> float:
    """Latest effective risk score, falling back to 7.0."""
    assessments = getattr(user, "effective_risk_assessments", None) or []
    if assessments:
        latest = max(assessments, key=lambda a: getattr(a, "created_at", 0))
        score = getattr(latest, "effective_risk_score", None)
        if score is not None:
            return float(score)
    return 7.0


def _total_corpus(user: Any) -> float:
    """Sum of all portfolio current_values (or 0)."""
    portfolios = getattr(user, "portfolios", None) or []
    total = 0.0
    for p in portfolios:
        val = getattr(p, "current_value", None)
        if val is not None:
            total += float(val)
    return total


def _goals_list(user: Any) -> list[dict[str, Any]]:
    """Convert user's FinancialGoal ORM rows to plain dicts for the engine."""
    goals = getattr(user, "financial_goals", None) or []
    result = []
    today = date.today()
    for g in goals:
        target = getattr(g, "target_date", None)
        months = 0
        if target:
            months = max(0, (target.year - today.year) * 12 + (target.month - today.month))

        priority = getattr(g, "priority", None)
        priority_str = priority.value if hasattr(priority, "value") else str(priority or "negotiable")

        result.append({
            "goal_name": getattr(g, "goal_name", "Unnamed Goal"),
            "time_to_goal_months": months,
            "amount_needed": float(getattr(g, "present_value_amount", 0)),
            "goal_priority": priority_str,
            "investment_goal": "wealth_creation",
        })
    return result


def _goal_ids_by_name(user: Any) -> dict[str, Any]:
    """Map goal_name → FinancialGoal.id for linking run targets to canonical goals."""
    goals = getattr(user, "financial_goals", None) or []
    return {getattr(g, "goal_name", ""): getattr(g, "id", None) for g in goals}


def build_asset_allocation_input_for_user(user: Any) -> Tuple[Any, Dict[str, Any]]:
    """Build (engine_input, debug_dict) from the User ORM graph.

    Returns a tuple of:
      - engine_input: dict matching what asset_allocation_pydantic.pipeline expects
      - debug_dict: metadata for tracing / input_payload persistence
    """
    dob = getattr(user, "date_of_birth", None)
    if dob is None:
        raise ValueError("User date_of_birth is required for allocation input")

    age = _age_from_dob(dob)
    risk_score = _effective_risk_score(user)
    corpus = _total_corpus(user)
    goals = _goals_list(user)

    inv_profile = getattr(user, "investment_profile", None)
    occupation = getattr(inv_profile, "occupation", None) if inv_profile else None

    engine_input = {
        "age": age,
        "occupation": str(occupation) if occupation else "salaried",
        "effective_risk_score": risk_score,
        "total_corpus": corpus,
        "goals": goals,
    }

    debug_dict = {
        "user_id": str(getattr(user, "id", None)),
        "age": age,
        "risk_score": risk_score,
        "total_corpus": corpus,
        "goal_count": len(goals),
    }

    return engine_input, debug_dict
