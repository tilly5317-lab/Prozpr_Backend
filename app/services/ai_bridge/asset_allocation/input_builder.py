"""Build the allocation engine input from the ORM User graph.

Reads date_of_birth, investment_profile, personal_finance_profile,
tax_profile, financial_goals, portfolios, and effective_risk_assessment to
assemble the ``AllocationInput`` pydantic model the engine expects.
Falls back gracefully when the engine package is not installed.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Tuple

from app.services.ai_bridge.common import ensure_ai_agents_path

logger = logging.getLogger(__name__)

ensure_ai_agents_path()

try:
    from asset_allocation_pydantic.models import AllocationInput, Goal  # type: ignore[import-not-found]
    _models_available = True
except ImportError:
    _models_available = False


def _age_from_dob(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _effective_risk_score(user: Any) -> float:
    """Latest effective risk score, falling back to 7.0.

    Checks for a transient ``_chat_risk_score_override`` attribute first,
    set by the rebalancing counterfactual-explore flow when the customer
    asks "what if my risk score were X?".
    """
    override = getattr(user, "_chat_risk_score_override", None)
    if override is not None:
        return float(override)
    era = getattr(user, "effective_risk_assessment", None)
    if era is not None:
        score = getattr(era, "effective_risk_score", None)
        if score is not None:
            return float(score)
    return 7.0


def _risk_willingness(user: Any) -> float | None:
    era = getattr(user, "effective_risk_assessment", None)
    if era is not None:
        val = getattr(era, "risk_willingness", None)
        if val is not None:
            return float(val)
    return None


def _risk_capacity_score(user: Any) -> float | None:
    era = getattr(user, "effective_risk_assessment", None)
    if era is not None:
        val = getattr(era, "risk_capacity_score", None)
        if val is not None:
            return float(val)
    return None


def _era_output_field(user: Any, field: str, default: Any = None) -> Any:
    """Read a field from ``EffectiveRiskAssessment.output`` JSONB."""
    era = getattr(user, "effective_risk_assessment", None)
    if era is not None:
        output = getattr(era, "output", None) or {}
        if isinstance(output, dict):
            return output.get(field, default)
    return default


def _total_corpus_from_portfolios(user: Any) -> float:
    """Estimate corpus from portfolios.

    ``Portfolio`` stores the roll-up on ``total_value`` (there is no
    ``current_value`` on the portfolio row — that field lives on
    ``PortfolioHolding``). If ``total_value`` is zero, sum each holding's
    ``current_value`` so NAV-synced lines still count.
    """
    portfolios = getattr(user, "portfolios", None) or []
    total = 0.0
    for p in portfolios:
        tv = getattr(p, "total_value", None)
        if tv is not None:
            tvf = float(tv)
            if tvf > 0:
                total += tvf
                continue
        hsum = 0.0
        for h in getattr(p, "holdings", None) or []:
            cv = getattr(h, "current_value", None)
            if cv is not None:
                hsum += float(cv)
        if hsum > 0:
            total += hsum
    return total


def _sum_goal_present_values(user: Any) -> float:
    """Sum ``present_value_amount`` across active-ish goals — usable corpus proxy."""
    goals = getattr(user, "financial_goals", None) or []
    s = 0.0
    for g in goals:
        try:
            s += float(getattr(g, "present_value_amount", 0) or 0)
        except (TypeError, ValueError):
            continue
    return s


def _investable_assets(user: Any) -> float:
    inv = getattr(user, "investment_profile", None)
    if inv is None:
        return 0.0
    val = getattr(inv, "investable_assets", None)
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _target_corpus_from_profile(user: Any) -> float:
    inv = getattr(user, "investment_profile", None)
    if inv is None:
        return 0.0
    val = getattr(inv, "target_corpus", None)
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _effective_total_corpus(user: Any) -> tuple[float, str]:
    """Resolve corpus for the allocation engine when the first signal is weak.

    Order: sum of ``Portfolio.total_value`` (else holding ``current_value``)
    → ``investment_profile.investable_assets`` → sum of goal
    ``present_value_amount`` → ``target_corpus`` on investment profile.
    """
    from_portfolios = _total_corpus_from_portfolios(user)
    if from_portfolios > 0:
        return from_portfolios, "portfolio_current_value"

    inv_assets = _investable_assets(user)
    if inv_assets > 0:
        return inv_assets, "investment_profile_investable_assets"

    goals_sum = _sum_goal_present_values(user)
    if goals_sum > 0:
        return goals_sum, "goals_present_value_sum"

    target = _target_corpus_from_profile(user)
    if target > 0:
        return target, "investment_profile_target_corpus"

    return 0.0, "none"


def _annual_income(user: Any) -> float:
    """Annual income from investment_profile or personal_finance_profile."""
    inv = getattr(user, "investment_profile", None)
    if inv is not None:
        val = getattr(inv, "annual_income", None)
        if val is not None and float(val) > 0:
            return float(val)
    pfp = getattr(user, "personal_finance_profile", None)
    if pfp is not None:
        for attr in ("annual_income_min", "annual_income_max"):
            val = getattr(pfp, attr, None)
            if val is not None and float(val) > 0:
                return float(val)
    return 0.0


def _monthly_household_expense(user: Any) -> float:
    """Monthly expenses from investment_profile or personal_finance_profile."""
    inv = getattr(user, "investment_profile", None)
    if inv is not None:
        outgoings = getattr(inv, "regular_outgoings", None)
        if outgoings is not None and float(outgoings) > 0:
            return float(outgoings)
    pfp = getattr(user, "personal_finance_profile", None)
    if pfp is not None:
        exp_min = getattr(pfp, "annual_expense_min", None)
        if exp_min is not None and float(exp_min) > 0:
            return float(exp_min) / 12.0
        exp_max = getattr(pfp, "annual_expense_max", None)
        if exp_max is not None and float(exp_max) > 0:
            return float(exp_max) / 12.0
    return 0.0


def _tax_regime(user: Any) -> str:
    tp = getattr(user, "tax_profile", None)
    if tp is not None:
        regime = getattr(tp, "tax_regime", None)
        if regime and str(regime).lower() in ("old", "new"):
            return str(regime).lower()
    return "new"


def _effective_tax_rate(user: Any) -> float:
    tp = getattr(user, "tax_profile", None)
    if tp is not None:
        rate = getattr(tp, "income_tax_rate", None)
        if rate is not None:
            return min(float(rate), 100.0)
    return 30.0


def _section_80c_utilized(user: Any) -> float:
    tp = getattr(user, "tax_profile", None)
    if tp is not None:
        output = getattr(tp, "notes", None)
        if output and isinstance(output, str):
            pass
    return 0.0


def _net_financial_assets(user: Any) -> float | None:
    inv = getattr(user, "investment_profile", None)
    if inv is not None:
        val = getattr(inv, "investable_assets", None)
        if val is not None:
            return float(val)
    return None


def _goal_priority_for_pipeline(priority: Any) -> str:
    """Map ORM ``GoalPriority`` (only HIGH / MEDIUM / LOW) to pipeline ``Goal.goal_priority``.

    The engine contract is ``negotiable`` | ``non_negotiable`` only. We map
    HIGH → non_negotiable (must-fund in sizing); MEDIUM and LOW → negotiable.
    """
    raw = priority.value if hasattr(priority, "value") else str(priority or "").strip()
    key = raw.upper()
    if key == "HIGH":
        return "non_negotiable"
    if key in ("MEDIUM", "LOW"):
        return "negotiable"
    return "negotiable"


def _goals_list(user: Any) -> list[dict[str, Any]]:
    """Convert user's FinancialGoal ORM rows to plain dicts for the engine."""
    goals = getattr(user, "financial_goals", None) or []
    result = []
    today = date.today()
    for g in goals:
        target = getattr(g, "target_date", None)
        months = 0
        if target:
            months = max(1, (target.year - today.year) * 12 + (target.month - today.month))

        priority = getattr(g, "priority", None)

        result.append({
            "goal_name": getattr(g, "goal_name", "Unnamed Goal"),
            "time_to_goal_months": months,
            "amount_needed": float(getattr(g, "present_value_amount", 0) or 0),
            "goal_priority": _goal_priority_for_pipeline(priority),
            "investment_goal": str(getattr(g, "investment_goal", None) or "wealth_creation"),
        })
    return result


def _goal_ids_by_name(user: Any) -> dict[str, Any]:
    """Map goal_name → FinancialGoal.id for linking run targets to canonical goals."""
    goals = getattr(user, "financial_goals", None) or []
    return {getattr(g, "goal_name", ""): getattr(g, "id", None) for g in goals}


def build_asset_allocation_input_for_user(user: Any) -> Tuple[Any, Dict[str, Any], Dict[str, Any]]:
    """Build ``(engine_input, debug_dict, allocation_input_snapshot)`` from the User ORM graph.

    Returns a tuple of:
      - engine_input: ``AllocationInput`` pydantic model (or a plain dict if
        the engine package is not installed)
      - debug_dict: metadata for tracing
      - allocation_input_snapshot: JSON-serialisable dict of the full engine
        input for ``asset_allocation_runs.input_payload`` (replay).
    """
    dob = getattr(user, "date_of_birth", None)
    if dob is None:
        raise ValueError("User date_of_birth is required for allocation input")

    age = _age_from_dob(dob)
    risk_score = _effective_risk_score(user)
    corpus, corpus_source = _effective_total_corpus(user)
    from_portfolios = _total_corpus_from_portfolios(user)
    goals = _goals_list(user)
    income = _annual_income(user)
    monthly_expense = _monthly_household_expense(user)
    tax_reg = _tax_regime(user)
    tax_rate = _effective_tax_rate(user)
    occupation = getattr(user, "occupation", None)

    osi = _era_output_field(user, "osi", 0.5)
    sra = _era_output_field(user, "savings_rate_adjustment", "none")
    if sra not in ("none", "equity_boost", "equity_reduce", "skipped"):
        sra = "none"
    gap_exceeds = bool(_era_output_field(user, "gap_exceeds_3", False))

    input_kwargs: Dict[str, Any] = {
        "effective_risk_score": risk_score,
        "age": age,
        "annual_income": income,
        "osi": float(osi) if osi is not None else 0.5,
        "savings_rate_adjustment": sra,
        "gap_exceeds_3": gap_exceeds,
        "total_corpus": corpus,
        "monthly_household_expense": monthly_expense,
        "tax_regime": tax_reg,
        "effective_tax_rate": tax_rate,
        "section_80c_utilized": _section_80c_utilized(user),
        "goals": goals,
        "occupation_type": str(occupation) if occupation else None,
        "risk_willingness": _risk_willingness(user),
        "risk_capacity_score": _risk_capacity_score(user),
        "net_financial_assets": _net_financial_assets(user),
    }

    if _models_available:
        goal_objs = [Goal(**g) for g in goals]
        input_kwargs["goals"] = goal_objs
        engine_input = AllocationInput(**input_kwargs)
        allocation_snapshot = engine_input.model_dump(mode="json")
    else:
        engine_input = input_kwargs
        allocation_snapshot = {**input_kwargs}

    debug_dict = {
        "user_id": str(getattr(user, "id", None)),
        "age": age,
        "risk_score": risk_score,
        "total_corpus": corpus,
        "total_corpus_from_portfolios": from_portfolios,
        "total_corpus_source": corpus_source,
        "goal_count": len(goals),
        "annual_income": income,
        "monthly_household_expense": monthly_expense,
        "tax_regime": tax_reg,
        "effective_tax_rate": tax_rate,
        "osi": float(osi) if osi is not None else 0.5,
        "savings_rate_adjustment": sra,
    }

    if corpus <= 0:
        logger.warning(
            "allocation input: total_corpus is 0 after fallbacks (user_id=%s, source=%s)",
            getattr(user, "id", None),
            corpus_source,
        )
    elif corpus_source != "portfolio_current_value":
        logger.info(
            "allocation corpus inferred from %s (user_id=%s portfolio_sum=%s effective=%s)",
            corpus_source,
            getattr(user, "id", None),
            from_portfolios,
            corpus,
        )

    return engine_input, debug_dict, allocation_snapshot
