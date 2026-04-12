"""AI bridge — `effective_risk_from_user.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from risk_profiling.scoring import OSI_MAP, compute_all_scores

from Ideal_asset_allocation.models import AllocationInput, ShortTermExpense

_RISK_LEVEL_TO_WILLINGNESS = {
    0: 3.5,
    1: 5.0,
    2: 6.5,
    3: 8.0,
    4: 9.0,
}


def _normalize_occupation(raw: str | None) -> str:
    if not raw:
        return "private_sector"
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if key in OSI_MAP:
        return key
    aliases = {
        "private": "private_sector",
        "public": "public_sector",
        "govt": "public_sector",
        "government": "public_sector",
        "business": "family_business",
        "familybusiness": "family_business",
        "freelance": "freelancer_gig",
        "gig": "freelancer_gig",
        "retired": "retired_homemaker_student",
        "homemaker": "retired_homemaker_student",
        "student": "retired_homemaker_student",
        "commission": "commission_based",
    }
    return aliases.get(key, "private_sector")


def _clamp_score(x: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, round(float(x), 4)))


def build_allocation_input_for_user(user, user_question: str) -> tuple[AllocationInput, dict[str, Any]]:
    """
    Returns (AllocationInput, debug dict with scoring payload / fallbacks used).
    """
    del user_question  # reserved for future carve-out hints from chat
    rp = getattr(user, "risk_profile", None)
    inv = getattr(user, "investment_profile", None)
    goals = list(getattr(user, "financial_goals", []) or [])
    portfolios = list(getattr(user, "portfolios", []) or [])

    if getattr(user, "date_of_birth", None) is None:
        raise ValueError("missing_date_of_birth")

    _IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    age = max(18, datetime.now(_IST).year - user.date_of_birth.year)

    annual_income = float(getattr(inv, "annual_income", None) or 0.0)
    monthly_out = float(getattr(inv, "regular_outgoings", None) or 0.0)
    annual_expense = monthly_out * 12 if monthly_out > 0 else max(annual_income * 0.55, 1.0)

    financial_assets = float(getattr(inv, "investable_assets", None) or 0.0)
    if financial_assets <= 0:
        financial_assets = float(getattr(inv, "portfolio_value", None) or 0.0)

    if portfolios:
        primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
        tv = float(getattr(primary, "total_value", None) or 0.0)
        if tv > financial_assets:
            financial_assets = tv

    total_liab = float(getattr(inv, "total_liabilities", None) or 0.0)
    mortgage_bal = float(getattr(inv, "mortgage_amount", None) or 0.0)
    liabilities_excl = max(0.0, total_liab - mortgage_bal)
    annual_mortgage = float(getattr(inv, "annual_mortgage_payment", None) or 0.0)
    properties_owned = int(getattr(inv, "properties_owned", None) or 0)

    occupation_type = _normalize_occupation(
        getattr(rp, "occupation_type", None) if rp else None
    )

    rw = getattr(rp, "risk_willingness", None) if rp else None
    if rw is None and rp is not None and getattr(rp, "risk_level", None) is not None:
        rw = _RISK_LEVEL_TO_WILLINGNESS.get(int(rp.risk_level), 6.0)
    elif rw is None:
        rw = 6.0
    risk_willingness = _clamp_score(float(rw))

    score_inputs = {
        "age": age,
        "occupation_type": occupation_type,
        "annual_income": annual_income,
        "annual_expense": annual_expense,
        "financial_assets": financial_assets,
        "liabilities_excluding_mortgage": liabilities_excl,
        "annual_mortgage_payment": annual_mortgage,
        "properties_owned": properties_owned,
        "risk_willingness": risk_willingness,
    }

    score_pkg: dict[str, Any] | None = None
    try:
        score_pkg = compute_all_scores(score_inputs)
    except Exception:
        score_pkg = None

    debug: dict[str, Any] = {"score_inputs": score_inputs, "compute_all_scores": score_pkg}

    if score_pkg:
        out = score_pkg["output"]
        calc = score_pkg["calculations"]
        effective_risk_score = _clamp_score(float(out["effective_risk_score"]))
        osi = float(calc["osi"])
        savings_rate_adjustment = calc["savings_rate_adjustment"]
        gap_exceeds_3 = bool(calc["gap_exceeds_3"])
        nfa = float(calc.get("net_financial_assets", 0.0))
        shortfall_amount = abs(nfa) if nfa < 0 else None
        risk_willingness_opt = float(risk_willingness)
        risk_capacity_score_opt = float(calc["risk_capacity_score_clamped"])
        savings_rate_opt = calc.get("savings_rate")
        net_financial_assets_opt = nfa
    else:
        effective_risk_score = _clamp_score(risk_willingness)
        osi = float(OSI_MAP[occupation_type])
        savings_rate_adjustment = "skipped"
        gap_exceeds_3 = False
        shortfall_amount = None
        risk_willingness_opt = float(risk_willingness)
        risk_capacity_score_opt = effective_risk_score
        savings_rate_opt = None
        net_financial_assets_opt = None

    horizon_raw = None
    if inv:
        horizon_raw = getattr(inv, "total_horizon", None)
    if not horizon_raw and rp:
        horizon_raw = getattr(rp, "investment_horizon", None)
    investment_horizon = (horizon_raw or "10 years").strip()

    horizon_years = None
    m = re.search(r"(\d+(?:\.\d+)?)", investment_horizon)
    if m:
        try:
            horizon_years = float(m.group(1))
        except ValueError:
            horizon_years = None

    goal_names = [
        getattr(g, "goal_name", None) or getattr(g, "name", None)
        for g in goals
        if getattr(g, "goal_name", None) or getattr(g, "name", None)
    ]
    investment_goal = goal_names[0] if goal_names else "wealth_creation"

    monthly_household_expense = monthly_out if monthly_out > 0 else max(annual_expense / 12, 1.0)

    total_corpus = max(financial_assets, 1.0)

    short_term: list[ShortTermExpense] = []
    planned = float(getattr(inv, "planned_major_expenses", None) or 0.0) if inv else 0.0
    if planned > 0:
        short_term.append(ShortTermExpense(amount=planned, timeline_in_months=18))

    alloc_in = AllocationInput(
        effective_risk_score=effective_risk_score,
        age=age,
        annual_income=max(annual_income, 1.0),
        osi=osi,
        savings_rate_adjustment=savings_rate_adjustment,
        gap_exceeds_3=gap_exceeds_3,
        shortfall_amount=shortfall_amount,
        total_corpus=total_corpus,
        monthly_household_expense=monthly_household_expense,
        investment_horizon=investment_horizon,
        investment_horizon_years=horizon_years,
        investment_goal=investment_goal,
        tax_regime="new",
        section_80c_utilized=0.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        short_term_expenses=short_term,
        risk_willingness=risk_willingness_opt,
        risk_capacity_score=risk_capacity_score_opt,
        savings_rate=savings_rate_opt,
        net_financial_assets=net_financial_assets_opt,
        occupation_type=occupation_type,
    )
    return alloc_in, debug
