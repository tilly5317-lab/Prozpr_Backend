"""Composite retirement-corpus primitive."""
from __future__ import annotations
import numpy_financial as npf


def retirement_corpus_pv(
    annual_expense_fv: float,
    post_retirement_years: int,
    real_roi_annual: float,
) -> float:
    """Compute the corpus required at retirement-start to fund annual expenses for N years.

    PV of an annuity: corpus = expense × [1 - (1+r)^(-n)] / r
    """
    if real_roi_annual == 0:
        return annual_expense_fv * post_retirement_years
    return float(-npf.pv(real_roi_annual, post_retirement_years, annual_expense_fv))
