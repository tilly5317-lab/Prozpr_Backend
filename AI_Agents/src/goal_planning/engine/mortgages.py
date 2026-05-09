"""Stage 3 + 4 mortgage helpers: RATE inversion, amortization (monthly + annual)."""
from __future__ import annotations
from datetime import date
from calendar import monthrange

from goal_planning.models import CurrentProperty, MortgageAmortizationRow
from goal_planning.engine._types import RunContext, MortgageSchedule, MortgageAnnualRow
from goal_planning.engine.dates import fy_end_after
from financial_primitives.annuity import pmt, rate, ipmt, RATEConvergenceError


DEFAULT_FALLBACK_RATE_ANNUAL = 0.075


def _months_between(start: date, end: date) -> int:
    """Inclusive whole-month count between two dates (start -> end)."""
    return (end.year - start.year) * 12 + (end.month - start.month)


def _add_months(d: date, months: int) -> date:
    """Add N months and return end-of-month date."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day)


def build_existing_mortgages(
    properties: list[CurrentProperty],
    ctx: RunContext,
    warnings: list[str],
) -> list[MortgageSchedule]:
    schedules: list[MortgageSchedule] = []
    for p in properties:
        if not p.has_mortgage:
            continue
        if p.mortgage_balance is None or p.mortgage_emi is None or p.mortgage_last_date is None:
            warnings.append(f"existing:{p.name} missing mortgage fields; skipping")
            continue
        as_of = p.mortgage_balance_as_of_date or ctx.latest_update_date
        if p.mortgage_last_date <= as_of:
            warnings.append(f"existing:{p.name} mortgage already paid off as of {p.mortgage_last_date}")
            continue

        months_remaining = _months_between(as_of, p.mortgage_last_date)
        if months_remaining <= 0:
            continue

        try:
            monthly_rate = rate(months_remaining, p.mortgage_emi, p.mortgage_balance)
        except RATEConvergenceError as e:
            warnings.append(
                f"existing:{p.name} mortgage rate inversion did not converge ({e}); "
                f"falling back to default {DEFAULT_FALLBACK_RATE_ANNUAL:.1%}"
            )
            monthly_rate = (1 + DEFAULT_FALLBACK_RATE_ANNUAL) ** (1/12) - 1

        monthly_rows = _amortize_monthly(
            start=as_of, principal=p.mortgage_balance,
            monthly_rate=monthly_rate, emi=p.mortgage_emi,
            n_months=months_remaining,
        )
        annual_rows = _aggregate_annual(monthly_rows)
        schedules.append(MortgageSchedule(
            property_ref=f"existing:{p.name}",
            start_date=as_of,
            monthly_rows=monthly_rows,
            annual_rows=annual_rows,
        ))
    return schedules


def _amortize_monthly(
    start: date, principal: float, monthly_rate: float, emi: float, n_months: int,
) -> list[MortgageAmortizationRow]:
    rows: list[MortgageAmortizationRow] = []
    balance = principal
    for i in range(n_months):
        month_end = _add_months(start, i + 1)
        interest = balance * monthly_rate
        principal_portion = min(emi - interest, balance)
        actual_emi = interest + principal_portion
        new_balance = max(balance - principal_portion, 0.0)
        rows.append(MortgageAmortizationRow(
            month_end=month_end,
            opening_balance=balance,
            emi=actual_emi,
            interest_portion=interest,
            principal_portion=principal_portion,
            closing_balance=new_balance,
        ))
        balance = new_balance
        if balance <= 0:
            break
    return rows


def _aggregate_annual(monthly_rows: list[MortgageAmortizationRow]) -> list[MortgageAnnualRow]:
    by_fy: dict[date, list[MortgageAmortizationRow]] = {}
    for r in monthly_rows:
        fy = fy_end_after(r.month_end)
        by_fy.setdefault(fy, []).append(r)
    annual: list[MortgageAnnualRow] = []
    for fy_end in sorted(by_fy):
        rows = by_fy[fy_end]
        opening = rows[0].opening_balance
        closing = rows[-1].closing_balance
        interest = sum(r.interest_portion for r in rows)
        principal = sum(r.principal_portion for r in rows)
        emi_total = sum(r.emi for r in rows)
        annual.append(MortgageAnnualRow(
            fy_end=fy_end, opening_balance=opening, annual_interest=interest,
            annual_principal=principal, annual_emi_total=emi_total, closing_balance=closing,
        ))
    return annual
