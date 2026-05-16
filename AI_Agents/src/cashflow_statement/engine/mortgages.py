"""Mortgage helpers for existing + goal-property mortgages.

Both paths use the same simple model: a constant EMI flows out each month from
start_date through end_date. No forward amortization simulation — EMI is constant
(by construction or by user statement) and end_date is known directly.

Existing mortgages (Option X): user provides `mortgage_emi` and `mortgage_end_date`.
Goal-property mortgages: EMI is `pmt(rate, tenure, principal)`; end_date is
`start_date + tenure_months`.

Monthly rate convention: simple (`annual / 12`), matching Indian banking "monthly
reducing balance". The PMT formula uses the same convention.
"""
from __future__ import annotations
from datetime import date
from calendar import monthrange

from cashflow_statement.models import CurrentProperty
from cashflow_statement.engine._types import RunContext, MortgageSchedule
from cashflow_statement.engine.dates import fy_end_after
from financial_primitives.annuity import pmt


def _add_months(d: date, months: int) -> date:
    """Add N months and return end-of-month date."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day)


def _accrue_constant_emi_by_fy(
    start_date: date, end_date: date, emi: float, horizon_end: date | None = None,
) -> tuple[dict[date, float], date | None]:
    """Walk month-ends from `start_date` to `min(end_date, horizon_end)`, summing `emi` per FY.

    Returns (annual_emi_by_fy, effective_end_date). effective_end_date is `None` when
    `horizon_end` cuts the mortgage short before `end_date`.
    """
    annual_emi_by_fy: dict[date, float] = {}
    last_me: date | None = None
    i = 0
    while True:
        me = _add_months(start_date, i + 1)
        if me > end_date:
            break
        if horizon_end is not None and me > horizon_end:
            return annual_emi_by_fy, None
        fy = fy_end_after(me)
        annual_emi_by_fy[fy] = annual_emi_by_fy.get(fy, 0.0) + emi
        last_me = me
        i += 1
    return annual_emi_by_fy, last_me


def build_existing_mortgages(
    properties: list[CurrentProperty],
    ctx: RunContext,
    warnings: list[str],
) -> list[MortgageSchedule]:
    """Project per-FY EMI outflows for each existing mortgage (Option X).

    The engine trusts `mortgage_emi` and `mortgage_end_date` as user-provided.
    """
    schedules: list[MortgageSchedule] = []
    for p in properties:
        if not p.has_mortgage:
            continue
        assert p.mortgage_emi is not None and p.mortgage_end_date is not None

        if p.mortgage_end_date <= ctx.latest_update_date:
            warnings.append(
                f"existing:{p.name}: mortgage_end_date {p.mortgage_end_date} is in the past; "
                f"no EMI outflows projected"
            )
            continue

        annual_emi_by_fy, _ = _accrue_constant_emi_by_fy(
            start_date=ctx.latest_update_date,
            end_date=p.mortgage_end_date,
            emi=p.mortgage_emi,
        )

        schedules.append(MortgageSchedule(
            property_ref=f"existing:{p.name}",
            start_date=ctx.latest_update_date,
            end_date=p.mortgage_end_date,
            annual_emi_by_fy=annual_emi_by_fy,
        ))
    return schedules


def build_goal_property_mortgage(
    *,
    property_ref: str,
    start_date: date,
    principal: float,
    monthly_rate: float,
    tenure_months: int,
    horizon_end: date,
) -> MortgageSchedule:
    """Construct a goal-property mortgage schedule.

    EMI is PMT-derived (constant by construction). End date is start + tenure_months
    (deterministic). Per-FY EMI accrual capped at `horizon_end`: if the mortgage
    tail extends past horizon, `end_date` is None.
    """
    if principal <= 0 or tenure_months <= 0:
        return MortgageSchedule(
            property_ref=property_ref, start_date=start_date,
            end_date=None, annual_emi_by_fy={},
        )
    emi = pmt(monthly_rate, tenure_months, principal)
    analytical_end = _add_months(start_date, tenure_months)
    annual_emi_by_fy, _ = _accrue_constant_emi_by_fy(
        start_date=start_date,
        end_date=analytical_end,
        emi=emi,
        horizon_end=horizon_end,
    )
    end_date = analytical_end if analytical_end <= horizon_end else None
    return MortgageSchedule(
        property_ref=property_ref, start_date=start_date,
        end_date=end_date, annual_emi_by_fy=annual_emi_by_fy,
    )
