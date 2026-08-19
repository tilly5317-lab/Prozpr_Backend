"""Deterministic goal arithmetic — inflation, loan split, EMI.

Pure functions, no LLM and no I/O. This module exists because the model must
never do the arithmetic: asked to convert "2.4 lakh a month" into an annual
figure, Haiku returned a number ten times too large at 0.95 confidence, and a
digit-count slip is indistinguishable from a correct answer. The LLM's job is
to read WHAT the customer said; every number derived from it is computed here.

Rupee conventions match the rest of the codebase: amounts are floats in rupees,
rates are percentages (6.0 means 6%, not 0.06) because that is what the
``financial_goals`` columns and the profile forms store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# What a category's price typically does per year, when the customer has no
# view of their own. Deliberately conservative and deliberately visible — every
# one of these is shown to the customer as a changeable assumption, never
# applied silently.
DEFAULT_INFLATION_BY_TYPE: dict[str, float] = {
    "VEHICLE": 6.0,
    "HOME_PURCHASE": 7.0,
    "CHILD_EDUCATION": 10.0,
    "WEDDING": 7.0,
    "TRAVEL": 6.0,
    "RETIREMENT": 6.0,
    "EMERGENCY_FUND": 6.0,
    "WEALTH_CREATION": 6.0,
    "OTHER": 6.0,
}

DEFAULT_INFLATION = 6.0

# Sanity rails. A figure outside these is a mis-parse, not a customer with
# unusual plans, and is sent back as a question rather than stored.
MIN_YEARS = 0.5
MAX_YEARS = 60.0
MAX_COST = 1_000_00_00_000.0  # ₹1,000 crore
MAX_INTEREST = 36.0


def future_value(present_value: float, inflation_pct: float, years: float) -> float:
    """What ``present_value`` costs after ``years`` of ``inflation_pct``."""
    return float(present_value) * (1.0 + float(inflation_pct) / 100.0) ** float(years)


def emi(principal: float, annual_interest_pct: float, tenure_years: float) -> float:
    """Equated monthly instalment for a reducing-balance loan.

    Zero-interest loans divide evenly rather than dividing by zero — a real
    case in India (many vehicle and consumer-durable schemes are 0%).
    """
    n = round(float(tenure_years) * 12)
    if n <= 0:
        return 0.0
    p = float(principal)
    r = float(annual_interest_pct) / 100.0 / 12.0
    if r == 0:
        return p / n
    growth = (1.0 + r) ** n
    return p * r * growth / (growth - 1.0)


def years_between(start: date, end: date) -> float:
    """Fractional years, on the 365.25-day convention the engine uses."""
    return (end - start).days / 365.25


def target_date_from_years(years: float, *, today: date | None = None) -> date:
    from datetime import timedelta

    anchor = today or date.today()
    return anchor + timedelta(days=round(float(years) * 365.25))


def project_corpus(
    *,
    current_assets: float,
    monthly_sip: float,
    years: float,
    annual_return_pct: float,
) -> float:
    """What today's savings plus an ongoing SIP grow into over ``years``.

    A lump sum compounding, plus a monthly annuity compounding at the same
    monthly rate. Used to answer "will I already have enough for this?" BEFORE
    asking whether they want a loan — telling someone they can pay cash is a
    better conversation than asking them to design a loan they may not need.

    Deliberately a plain projection, not the full cashflow engine: it runs
    mid-conversation on every goal, and it must work for a customer whose
    profile is too thin for the engine to run at all.
    """
    r_annual = float(annual_return_pct) / 100.0
    n_months = max(0, round(float(years) * 12))
    lump = float(current_assets) * (1.0 + r_annual) ** float(years)
    if n_months == 0 or monthly_sip <= 0:
        return lump
    r_m = (1.0 + r_annual) ** (1.0 / 12.0) - 1.0
    if r_m == 0:
        return lump + float(monthly_sip) * n_months
    annuity = float(monthly_sip) * (((1.0 + r_m) ** n_months - 1.0) / r_m)
    return lump + annuity


@dataclass(frozen=True)
class GoalProjection:
    """Everything derived from a goal draft, ready to show the customer."""

    cost_pv: float
    cost_fv: float
    inflation_pct: float
    years: float
    target_date: date
    financed: bool
    down_payment: float
    loan_amount: float
    interest_pct: float | None
    tenure_years: float | None
    monthly_emi: float | None
    total_interest: float | None
    # What the customer actually has to SAVE — the corpus this goal asks of the
    # plan. For a financed purchase that is the down payment, not the sticker
    # price: the rest is borrowed, and borrowing is an EMI problem, not a
    # corpus problem.
    corpus_required: float

    @property
    def corpus_is_downpayment(self) -> bool:
        return self.financed


def project_goal(
    *,
    cost_pv: float,
    years: float,
    inflation_pct: float | None = None,
    goal_type: str = "OTHER",
    financed: bool = False,
    down_payment: float | None = None,
    down_payment_pct: float | None = None,
    interest_pct: float | None = None,
    tenure_years: float | None = None,
    today: date | None = None,
) -> GoalProjection:
    """Turn a goal draft into the numbers the customer is shown and the plan uses.

    ``down_payment`` is read as a NOMINAL amount at purchase time — when someone
    says "I'll put fifty lakh down" they mean fifty lakh of the money they will
    hand over, not fifty lakh of today's purchasing power. ``down_payment_pct``
    is applied to the inflated price instead, which is what a dealer's 20%-down
    quote actually means.
    """
    infl = (
        float(inflation_pct)
        if inflation_pct is not None
        else DEFAULT_INFLATION_BY_TYPE.get(goal_type.upper(), DEFAULT_INFLATION)
    )
    cost_fv = future_value(cost_pv, infl, years)
    target = target_date_from_years(years, today=today)

    if not financed:
        return GoalProjection(
            cost_pv=float(cost_pv),
            cost_fv=cost_fv,
            inflation_pct=infl,
            years=float(years),
            target_date=target,
            financed=False,
            down_payment=cost_fv,
            loan_amount=0.0,
            interest_pct=None,
            tenure_years=None,
            monthly_emi=None,
            total_interest=None,
            corpus_required=cost_fv,
        )

    if down_payment is not None:
        down = float(down_payment)
    elif down_payment_pct is not None:
        down = cost_fv * float(down_payment_pct) / 100.0
    else:
        down = 0.0
    # A down payment above the inflated price is a mis-parse or a customer who
    # does not need a loan at all; clamp so the loan can never go negative.
    down = max(0.0, min(down, cost_fv))
    loan = max(0.0, cost_fv - down)

    monthly = None
    total_int = None
    if loan > 0 and interest_pct is not None and tenure_years:
        monthly = emi(loan, interest_pct, tenure_years)
        total_int = monthly * round(float(tenure_years) * 12) - loan

    return GoalProjection(
        cost_pv=float(cost_pv),
        cost_fv=cost_fv,
        inflation_pct=infl,
        years=float(years),
        target_date=target,
        financed=True,
        down_payment=down,
        loan_amount=loan,
        interest_pct=float(interest_pct) if interest_pct is not None else None,
        tenure_years=float(tenure_years) if tenure_years else None,
        monthly_emi=monthly,
        total_interest=total_int,
        corpus_required=down,
    )


__all__ = [
    "DEFAULT_INFLATION",
    "DEFAULT_INFLATION_BY_TYPE",
    "GoalProjection",
    "MAX_COST",
    "MAX_INTEREST",
    "MAX_YEARS",
    "MIN_YEARS",
    "emi",
    "future_value",
    "project_corpus",
    "project_goal",
    "target_date_from_years",
    "years_between",
]
