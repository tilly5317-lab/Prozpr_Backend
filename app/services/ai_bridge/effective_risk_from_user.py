"""Convert a User ORM object into an AllocationInput for the allocation pipeline.

Reads risk_profile, investment_profile, financial_goals, and portfolios from
the user, computes the effective risk score via ``risk_profiling.scoring``, and
returns a ready-to-invoke ``AllocationInput`` plus a debug dict.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from risk_profiling.scoring import OSI_MAP, compute_all_scores
from Ideal_asset_allocation.models import AllocationInput, ShortTermExpense

# Maps the discrete risk_level (0-4) stored in risk_profile to a 1-10 willingness score.
_RISK_LEVEL_TO_WILLINGNESS: dict[int, float] = {
    0: 3.5, 1: 5.0, 2: 6.5, 3: 8.0, 4: 9.0,
}

# Normalise free-text occupation strings to keys accepted by OSI_MAP.
_OCCUPATION_ALIASES: dict[str, str] = {
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


def _normalize_occupation(raw: str | None) -> str:
    """Map a raw occupation string to an OSI_MAP key."""
    if not raw:
        return "private_sector"
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if key in OSI_MAP:
        return key
    return _OCCUPATION_ALIASES.get(key, "private_sector")


def _clamp(x: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, round(float(x), 4)))


# ---------------------------------------------------------------------------
# Helpers to safely pull numbers from ORM objects that may have None fields.
# ---------------------------------------------------------------------------

def _f(obj, attr: str, default: float = 0.0) -> float:
    """Safe float getter."""
    return float(getattr(obj, attr, None) or default)


def _i(obj, attr: str, default: int = 0) -> int:
    """Safe int getter."""
    return int(getattr(obj, attr, None) or default)


def build_allocation_input_for_user(
    user, user_question: str,
) -> tuple[AllocationInput, dict[str, Any]]:
    """Build ``AllocationInput`` + debug payload from a User ORM row.

    Raises ``ValueError`` if ``user.date_of_birth`` is missing.
    """
    del user_question  # reserved for future carve-out hints from chat

    rp = getattr(user, "risk_profile", None)
    inv = getattr(user, "investment_profile", None)
    goals = list(getattr(user, "financial_goals", []) or [])
    portfolios = list(getattr(user, "portfolios", []) or [])

    if getattr(user, "date_of_birth", None) is None:
        raise ValueError("missing_date_of_birth")

    # -- Age (minimum 18) -------------------------------------------------------
    _IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    age = max(18, datetime.now(_IST).year - user.date_of_birth.year)

    # -- Income / expenses ------------------------------------------------------
    annual_income = _f(inv, "annual_income")
    monthly_out = _f(inv, "regular_outgoings")
    annual_expense = monthly_out * 12 if monthly_out > 0 else max(annual_income * 0.55, 1.0)

    # -- Financial assets (best available source) --------------------------------
    financial_assets = _f(inv, "investable_assets") or _f(inv, "portfolio_value")
    if portfolios:
        primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
        financial_assets = max(financial_assets, _f(primary, "total_value"))

    # -- Liabilities & property --------------------------------------------------
    total_liab = _f(inv, "total_liabilities")
    mortgage_bal = _f(inv, "mortgage_amount")
    liabilities_excl = max(0.0, total_liab - mortgage_bal)

    # -- Occupation & risk willingness -------------------------------------------
    occupation_type = _normalize_occupation(getattr(rp, "occupation_type", None) if rp else None)

    rw = getattr(rp, "risk_willingness", None) if rp else None
    if rw is None and rp is not None and getattr(rp, "risk_level", None) is not None:
        rw = _RISK_LEVEL_TO_WILLINGNESS.get(int(rp.risk_level), 6.0)
    risk_willingness = _clamp(float(rw or 6.0))

    # -- Risk scoring via risk_profiling.scoring ---------------------------------
    score_inputs = {
        "age": age,
        "occupation_type": occupation_type,
        "annual_income": annual_income,
        "annual_expense": annual_expense,
        "financial_assets": financial_assets,
        "liabilities_excluding_mortgage": liabilities_excl,
        "annual_mortgage_payment": _f(inv, "annual_mortgage_payment"),
        "properties_owned": _i(inv, "properties_owned"),
        "risk_willingness": risk_willingness,
    }

    try:
        score_pkg = compute_all_scores(score_inputs)
    except Exception:
        score_pkg = None

    debug: dict[str, Any] = {"score_inputs": score_inputs, "compute_all_scores": score_pkg}

    if score_pkg:
        out = score_pkg["output"]
        calc = score_pkg["calculations"]
        nfa = float(calc.get("net_financial_assets", 0.0))
        effective_risk_score = _clamp(float(out["effective_risk_score"]))
        osi = float(calc["osi"])
        savings_rate_adjustment = calc["savings_rate_adjustment"]
        gap_exceeds_3 = bool(calc["gap_exceeds_3"])
        shortfall_amount = abs(nfa) if nfa < 0 else None
        risk_capacity_score_opt = float(calc["risk_capacity_score_clamped"])
        savings_rate_opt = calc.get("savings_rate")
        net_financial_assets_opt = nfa
    else:
        # Fallback when scoring module fails.
        effective_risk_score = _clamp(risk_willingness)
        osi = float(OSI_MAP[occupation_type])
        savings_rate_adjustment = "skipped"
        gap_exceeds_3 = False
        shortfall_amount = None
        risk_capacity_score_opt = effective_risk_score
        savings_rate_opt = None
        net_financial_assets_opt = None

    # -- Investment horizon & goal -----------------------------------------------
    horizon_raw = (getattr(inv, "total_horizon", None) if inv else None) or (
        getattr(rp, "investment_horizon", None) if rp else None
    )
    investment_horizon = (horizon_raw or "10 years").strip()

    horizon_years: float | None = None
    m = re.search(r"(\d+(?:\.\d+)?)", investment_horizon)
    if m:
        try:
            horizon_years = float(m.group(1))
        except ValueError:
            pass

    goal_names = [
        getattr(g, "goal_name", None) or getattr(g, "name", None)
        for g in goals if getattr(g, "goal_name", None) or getattr(g, "name", None)
    ]

    # -- Short-term expense carve-outs -------------------------------------------
    short_term: list[ShortTermExpense] = []
    planned = _f(inv, "planned_major_expenses") if inv else 0.0
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
        total_corpus=max(financial_assets, 1.0),
        monthly_household_expense=monthly_out if monthly_out > 0 else max(annual_expense / 12, 1.0),
        investment_horizon=investment_horizon,
        investment_horizon_years=horizon_years,
        investment_goal=goal_names[0] if goal_names else "wealth_creation",
        tax_regime="new",
        section_80c_utilized=0.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        short_term_expenses=short_term,
        risk_willingness=float(risk_willingness),
        risk_capacity_score=risk_capacity_score_opt,
        savings_rate=savings_rate_opt,
        net_financial_assets=net_financial_assets_opt,
        occupation_type=occupation_type,
    )
    return alloc_in, debug
