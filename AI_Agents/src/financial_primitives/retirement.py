"""Composite retirement-corpus primitive."""
from __future__ import annotations


def retirement_corpus_pv(
    annual_expense_fv: float,
    post_retirement_years: int,
    real_roi_annual: float,
) -> float:
    """Compute the corpus required at retirement-start to fund annual expenses for N years.

    Closed-form PV of an ordinary annuity:
        corpus = expense × [1 − (1+r)^(−n)] / r

    Special case r=0: corpus = expense × n (no growth, just sum the payments).
    """
    if post_retirement_years <= 0:
        return 0.0
    if real_roi_annual == 0:
        return annual_expense_fv * post_retirement_years
    return annual_expense_fv * (1 - (1 + real_roi_annual) ** (-post_retirement_years)) / real_roi_annual
