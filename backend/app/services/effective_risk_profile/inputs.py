"""Effective risk profile — `inputs.py`.

App-layer persistence and calculation helpers for the user’s effective risk assessment (distinct from the deterministic ``risk_profiling.scoring`` used when building ``AllocationInput`` for ideal allocation).
"""


from __future__ import annotations

from datetime import date
from typing import Optional

from app.models.profile import InvestmentProfile, PersonalFinanceProfile, RiskProfile
from app.models.user import User
from app.services.effective_risk_profile.calculation import (
    EffectiveRiskComputationInput,
    risk_willingness_from_risk_level,
)


def _age_from_dob(dob: date, as_of: Optional[date] = None) -> float:
    as_of = as_of or date.today()
    days = (as_of - dob).days
    return max(0.0, float(days) / 365.25)


def _mid_or_none(lo: Optional[float], hi: Optional[float]) -> Optional[float]:
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)
    if hi is not None:
        return float(hi)
    return None


def derive_annual_income(profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]) -> float:
    if inv and inv.annual_income is not None:
        return float(inv.annual_income)
    mid = _mid_or_none(
        profile.annual_income_min if profile else None,
        profile.annual_income_max if profile else None,
    )
    return float(mid) if mid is not None else 0.0


def derive_annual_expense(profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]) -> float:
    mid = _mid_or_none(
        profile.annual_expense_min if profile else None,
        profile.annual_expense_max if profile else None,
    )
    if mid is not None:
        return float(mid)
    if inv and inv.regular_outgoings is not None:
        return float(inv.regular_outgoings) * 12.0
    return 0.0


def derive_liabilities_excluding_mortgage(inv: Optional[InvestmentProfile]) -> float:
    if not inv:
        return 0.0
    tl = float(inv.total_liabilities or 0)
    ma = float(inv.mortgage_amount or 0)
    if ma > 0 and tl >= ma:
        return max(0.0, tl - ma)
    return max(0.0, tl)


def derive_risk_willingness(risk: Optional[RiskProfile]) -> float:
    if risk is None:
        return 5.0
    if risk.risk_willingness is not None:
        return float(risk.risk_willingness)
    mapped = risk_willingness_from_risk_level(risk.risk_level)
    if mapped is not None:
        return mapped
    return 5.0


def derive_occupation_type(risk: Optional[RiskProfile]) -> str:
    if risk and risk.occupation_type:
        return str(risk.occupation_type)
    return "private_sector"


def build_computation_input(
    user: Optional[User],
    profile: Optional[PersonalFinanceProfile],
    inv: Optional[InvestmentProfile],
    risk: Optional[RiskProfile],
    *,
    as_of: Optional[date] = None,
) -> tuple[Optional[EffectiveRiskComputationInput], Optional[str]]:
    """
    Returns (input, error_reason). Error when DOB is missing (age required).
    """
    user_dob = getattr(user, "date_of_birth", None)
    if user_dob is None:
        return None, "date_of_birth_required"

    age = _age_from_dob(user_dob, as_of=as_of)

    annual_income = derive_annual_income(profile, inv)
    annual_expense = derive_annual_expense(profile, inv)
    financial_assets = float(inv.investable_assets) if inv and inv.investable_assets is not None else 0.0
    liabilities_ex = derive_liabilities_excluding_mortgage(inv)
    annual_mortgage_payment = float(inv.annual_mortgage_payment) if inv and inv.annual_mortgage_payment is not None else 0.0
    properties_owned = int(inv.properties_owned) if inv and inv.properties_owned is not None else 0

    inp = EffectiveRiskComputationInput(
        age=age,
        occupation_type=derive_occupation_type(risk),
        annual_income=annual_income,
        annual_expense=annual_expense,
        financial_assets=financial_assets,
        liabilities_excluding_mortgage=liabilities_ex,
        annual_mortgage_payment=annual_mortgage_payment,
        properties_owned=properties_owned,
        risk_willingness=derive_risk_willingness(risk),
    )
    return inp, None
