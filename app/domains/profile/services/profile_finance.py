"""Resolve canonical personal / investment profile fields for engines and APIs.

This is the SINGLE source of truth for the household-finance scalars. Every
engine (cashflow, effective-risk, asset-allocation) and the IPS view must read
these through the functions here — never off ``investment_profile`` columns,
which were slimmed down (the canonical home is ``personal_finance_profiles``).
The ``*_pfp`` helpers take a ``PersonalFinanceProfile`` (or None) so callers
that loaded the row directly can reuse them; the dict/user helpers wrap them.
"""

from __future__ import annotations

from typing import Any, Optional

_DEFAULT_TAX_RATE = 0.25


def _f(value: Any, default: float = 0.0) -> float:
    return float(value) if value is not None else default


# ── Canonical household-finance scalars (from personal_finance_profiles) ──
def annual_income_pfp(pfp: Any) -> float:
    return max(_f(getattr(pfp, "annual_income", None)), 0.0)


def monthly_household_expense_pfp(pfp: Any) -> float:
    return max(_f(getattr(pfp, "monthly_household_expense", None)), 0.0)


def annual_household_expense_pfp(pfp: Any) -> float:
    return monthly_household_expense_pfp(pfp) * 12.0


def financial_assets_pfp(pfp: Any) -> float:
    return max(_f(getattr(pfp, "financial_assets", None)), 0.0)


def financial_liabilities_excl_mortgage_pfp(pfp: Any) -> float:
    return max(_f(getattr(pfp, "financial_liabilities_excl_mortgage", None)), 0.0)


def starting_monthly_investment_pfp(pfp: Any) -> Optional[float]:
    sip = getattr(pfp, "starting_monthly_investment", None)
    return float(sip) if sip is not None else None


def current_portfolio_corpus_pfp(pfp: Any) -> float:
    return max(_f(getattr(pfp, "current_portfolio_corpus", None)), 0.0)


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
    return {
        "annual_income": annual_income_pfp(pfp),
        "effective_tax_rate": effective_tax_rate_for_user(user),
        # Cash & liquid assets only. The current portfolio value is folded into the
        # cashflow starting corpus separately (see input_builder) so the live
        # portfolio stays the single source of truth for that figure.
        "financial_assets": financial_assets_pfp(pfp),
        "financial_liabilities_excl_mortgage": financial_liabilities_excl_mortgage_pfp(pfp),
        "monthly_household_expense": monthly_household_expense_pfp(pfp),
        "starting_monthly_investment": starting_monthly_investment_pfp(pfp),
    }


def current_properties_for_user(user: Any) -> list[Any]:
    inv = getattr(user, "investment_profile", None)
    if inv is None:
        return []
    return list(getattr(inv, "current_properties", None) or [])
