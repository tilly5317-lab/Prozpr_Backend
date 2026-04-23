"""Build a ``goal_based_allocation_pydantic.AllocationInput`` from a User ORM row.

Reads from persisted DB rows only — no call into ``risk_profiling.scoring``.
When an ``effective_risk_assessments`` row is absent, falls back to score 7.0.

Entry: ``build_goal_allocation_input_for_user(user)`` → ``(AllocationInput, debug)``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from goal_based_allocation_pydantic.models import AllocationInput, Goal


_DEFAULT_RISK_SCORE = 7.0
_DEFAULT_TAX_RATE = 30.0
_MIN_AGE = 18
_SYNTH_GOAL_HORIZON_MONTHS = 120


def _f(obj: Any, attr: str, default: float = 0.0) -> float:
    """Safe float getter."""
    return float(getattr(obj, attr, None) or default)


def _i(obj: Any, attr: str, default: int = 0) -> int:
    """Safe int getter."""
    return int(getattr(obj, attr, None) or default)


def _clamp_score(x: float) -> float:
    return max(1.0, min(10.0, round(float(x), 4)))


def _age_from_dob(dob: date) -> int:
    today = date.today()
    age = today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )
    return max(_MIN_AGE, age)


def _months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(1, months)


def _pick_total_corpus(inv: Any, portfolios: List[Any]) -> float:
    investable = _f(inv, "investable_assets")
    portfolio_value = _f(inv, "portfolio_value")
    primary_value = 0.0
    if portfolios:
        primary = next(
            (p for p in portfolios if getattr(p, "is_primary", False)),
            portfolios[0],
        )
        primary_value = _f(primary, "total_value")
    return max(investable, portfolio_value, primary_value)


def _map_goals(financial_goals: List[Any], total_corpus: float) -> List[Goal]:
    today = date.today()
    mapped: List[Goal] = []
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
        gt_val = gt.value if hasattr(gt, "value") else str(gt or "other")
        mapped.append(
            Goal(
                goal_name=getattr(g, "goal_name", None) or "goal",
                time_to_goal_months=_months_between(today, target),
                amount_needed=float(getattr(g, "present_value_amount", 0.0) or 0.0),
                goal_priority="non_negotiable",
                investment_goal=gt_val.lower(),
            )
        )
    if mapped:
        return mapped
    # Zero active goals → synthesize a long-term wealth creation goal.
    return [
        Goal(
            goal_name="Long-term wealth creation",
            time_to_goal_months=_SYNTH_GOAL_HORIZON_MONTHS,
            amount_needed=max(total_corpus, 1.0),
            goal_priority="non_negotiable",
            investment_goal="wealth_creation",
        )
    ]


def build_goal_allocation_input_for_user(
    user: Any,
) -> tuple[AllocationInput, Dict[str, Any]]:
    """Return ``(AllocationInput, debug)`` for a User ORM row.

    Raises ``ValueError("missing_date_of_birth")`` when DOB is absent.
    """
    if getattr(user, "date_of_birth", None) is None:
        raise ValueError("missing_date_of_birth")

    era = getattr(user, "effective_risk_assessment", None)
    inv = getattr(user, "investment_profile", None)
    rp = getattr(user, "risk_profile", None)
    tp = getattr(user, "tax_profile", None)
    financial_goals = list(getattr(user, "financial_goals", []) or [])
    portfolios = list(getattr(user, "portfolios", []) or [])

    defaults_applied: List[str] = []

    # Risk block — prefer ERA row; fall back to score 7.0 when absent.
    if era is not None:
        effective_risk_score = _clamp_score(
            float(getattr(era, "effective_risk_score", None) or _DEFAULT_RISK_SCORE)
        )
        risk_capacity_score: Optional[float] = (
            float(era.risk_capacity_score)
            if getattr(era, "risk_capacity_score", None) is not None
            else None
        )
        risk_willingness: Optional[float] = (
            float(era.risk_willingness)
            if getattr(era, "risk_willingness", None) is not None
            else None
        )
        calc = getattr(era, "calculations", None) or {}
        osi = float(calc.get("osi", 1.0))
        savings_rate_adjustment = calc.get("savings_rate_adjustment") or "skipped"
        if savings_rate_adjustment not in {"none", "equity_boost", "equity_reduce", "skipped"}:
            savings_rate_adjustment = "skipped"
        gap_exceeds_3 = bool(calc.get("gap_exceeds_3", False))
        shortfall_amount = calc.get("shortfall_amount")
        net_financial_assets: Optional[float] = (
            float(calc["net_financial_assets"])
            if calc.get("net_financial_assets") is not None
            else None
        )
    else:
        defaults_applied.append("effective_risk_assessment_missing")
        effective_risk_score = _DEFAULT_RISK_SCORE
        risk_capacity_score = None
        risk_willingness = None
        osi = 1.0
        savings_rate_adjustment = "skipped"
        gap_exceeds_3 = False
        shortfall_amount = None
        net_financial_assets = None

    age = _age_from_dob(user.date_of_birth)
    annual_income = _f(inv, "annual_income")
    monthly_household_expense = _f(inv, "regular_outgoings")
    total_corpus = _pick_total_corpus(inv, portfolios)

    if tp is not None and getattr(tp, "income_tax_rate", None) is not None:
        effective_tax_rate = float(tp.income_tax_rate)
    else:
        defaults_applied.append("tax_profile_missing")
        effective_tax_rate = _DEFAULT_TAX_RATE

    occupation_type = getattr(rp, "occupation_type", None) if rp is not None else None

    # Boolean/tax columns not yet modelled in DB — see design doc follow-ups.
    defaults_applied.extend(
        [
            "tax_regime=new",
            "section_80c_utilized=0",
            "emergency_fund_needed=False",
            "primary_income_from_portfolio=False",
            "intergenerational_transfer=False",
        ]
    )

    goals = _map_goals(financial_goals, total_corpus)

    alloc_input = AllocationInput(
        effective_risk_score=effective_risk_score,
        age=age,
        annual_income=max(annual_income, 0.0),
        osi=max(0.0, min(1.0, osi)),
        savings_rate_adjustment=savings_rate_adjustment,
        gap_exceeds_3=gap_exceeds_3,
        shortfall_amount=shortfall_amount,
        total_corpus=max(total_corpus, 0.0),
        monthly_household_expense=max(monthly_household_expense, 0.0),
        tax_regime="new",
        section_80c_utilized=0.0,
        emergency_fund_needed=False,
        primary_income_from_portfolio=False,
        intergenerational_transfer=False,
        effective_tax_rate=max(0.0, min(100.0, effective_tax_rate)),
        goals=goals,
        risk_willingness=risk_willingness,
        risk_capacity_score=risk_capacity_score,
        net_financial_assets=net_financial_assets,
        occupation_type=occupation_type,
    )

    debug: Dict[str, Any] = {
        "has_effective_risk_assessment": era is not None,
        "has_investment_profile": inv is not None,
        "has_risk_profile": rp is not None,
        "has_tax_profile": tp is not None,
        "active_goal_count": sum(1 for g in goals if g.goal_name != "Long-term wealth creation")
        if any(g.goal_name != "Long-term wealth creation" for g in goals)
        else 0,
        "synthesized_default_goal": len(financial_goals) == 0
        or all(
            (getattr(g, "status", None).value if hasattr(getattr(g, "status", None), "value") else str(getattr(g, "status", "") or ""))
            .upper()
            != "ACTIVE"
            for g in financial_goals
        ),
        "defaults_applied": defaults_applied,
    }
    return alloc_input, debug
