"""Effective risk profile — `inputs.py`.

App-layer persistence and calculation helpers for the user’s effective risk assessment (distinct from the deterministic ``risk_profiling.scoring`` used when building ``AllocationInput`` for ideal allocation).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.domains.profile.models import (
    InvestmentProfile,
    PersonalFinanceProfile,
    RiskProfile,
)
from app.domains.identity.models.user import User
from app.domains.profile.services import profile_finance as pf
from app.domains.profile.services._effective_risk.calculation import (
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


# These delegate to profile_finance — the single canonical source — so income /
# expense / assets / liabilities resolve identically here, in the cashflow
# engine, and in asset allocation. (inv kept in the signature for back-compat.)
def derive_annual_income(
    profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]
) -> float:
    return pf.annual_income_pfp(profile)


def derive_annual_expense(
    profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]
) -> float:
    return pf.annual_household_expense_pfp(profile)


def derive_financial_assets(
    profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]
) -> float:
    return pf.financial_assets_pfp(profile)


def derive_liabilities_excluding_mortgage(
    profile: Optional[PersonalFinanceProfile], inv: Optional[InvestmentProfile]
) -> float:
    return pf.financial_liabilities_excl_mortgage_pfp(profile)


def derive_risk_willingness(risk: Optional[RiskProfile]) -> float:
    if risk is None:
        return 5.0
    # An explicitly stored willingness (manual override; rarely set) wins.
    if risk.risk_willingness is not None:
        return float(risk.risk_willingness)
    # Primary path: the 4-question questionnaire model. Preference comes from
    # risk_level (onboarding); the three behavioural answers are the full option
    # sentences stored on the risk profile. Unanswered/unrecognised inputs are
    # ignored by the model. Lazy import — the ``risk_profiling`` package pulls in
    # LangChain at import time (see calculation.py), which must not run at
    # app startup.
    from risk_profiling.willingness import compute_risk_willingness

    modelled = compute_risk_willingness(
        risk_level=risk.risk_level,
        investment_experience=risk.investment_experience,
        investment_focus=risk.investment_focus,
        drop_reaction=risk.drop_reaction,
    )["risk_willingness"]
    if modelled is not None:
        return float(modelled)
    # Fallback: nothing answerable (no risk_level and no behavioural answers).
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
    financial_assets = derive_financial_assets(profile, inv)
    liabilities_ex = derive_liabilities_excluding_mortgage(profile, inv)
    # These columns were removed from InvestmentProfile (mortgage/property detail
    # now lives on user_current_properties, not eager-loaded here) — read
    # defensively so a missing attribute can never crash the recalc.
    annual_mortgage_payment = (
        float(getattr(inv, "annual_mortgage_payment", None) or 0.0) if inv else 0.0
    )
    properties_owned = int(getattr(inv, "properties_owned", None) or 0) if inv else 0

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
