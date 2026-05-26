"""Resolve canonical personal / investment profile fields for engines and APIs."""

from __future__ import annotations

from typing import Any

_DEFAULT_TAX_RATE = 0.25


def _f(value: Any, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def effective_tax_rate_for_user(user: Any) -> float:
    pfp = getattr(user, "personal_finance_profile", None)
    if pfp is not None and getattr(pfp, "effective_tax_rate", None) is not None:
        return max(0.0, min(1.0, _f(pfp.effective_tax_rate)))
    tp = getattr(user, "tax_profile", None)
    if tp is not None and getattr(tp, "income_tax_rate", None) is not None:
        return max(0.0, min(1.0, _f(tp.income_tax_rate) / 100.0))
    return _DEFAULT_TAX_RATE


def personal_finance_scalars(user: Any) -> dict[str, float | None]:
    """Single source for cashflow ``ClientProfile`` scalars."""
    pfp = getattr(user, "personal_finance_profile", None)
    if pfp is None:
        return {
            "annual_income": 0.0,
            "effective_tax_rate": effective_tax_rate_for_user(user),
            "financial_assets": 0.0,
            "financial_liabilities_excl_mortgage": 0.0,
            "monthly_household_expense": 0.0,
            "starting_monthly_investment": None,
        }
    sip = getattr(pfp, "starting_monthly_investment", None)
    return {
        "annual_income": max(_f(getattr(pfp, "annual_income", None)), 0.0),
        "effective_tax_rate": effective_tax_rate_for_user(user),
        "financial_assets": max(_f(getattr(pfp, "financial_assets", None)), 0.0),
        "financial_liabilities_excl_mortgage": max(
            _f(getattr(pfp, "financial_liabilities_excl_mortgage", None)), 0.0
        ),
        "monthly_household_expense": max(
            _f(getattr(pfp, "monthly_household_expense", None)), 0.0
        ),
        "starting_monthly_investment": float(sip) if sip is not None else None,
    }


def current_properties_for_user(user: Any) -> list[Any]:
    inv = getattr(user, "investment_profile", None)
    if inv is None:
        return []
    return list(getattr(inv, "current_properties", None) or [])
