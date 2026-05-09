"""Stage 6: cashflow projection — monthly + annual rows with FY step-ups."""
from __future__ import annotations
from datetime import date

from goal_planning.models import (
    AnnualCashflowRow, MonthlyCashflowRow, OneOffEvent,
)
from goal_planning.engine._types import RunContext, MortgageSchedule
from goal_planning.engine.dates import fy_for_date, eomonth


def compute_horizon_years(
    retirement_date: date,
    last_goal_fy: date,
    one_off_outflows: list[OneOffEvent],
    latest_update_date: date,
    cap: int = 80,
) -> int:
    """Horizon in years: max(retirement, last goal FY, last one-off outflow FY) capped at `cap`."""
    last_outflow_year = 0
    if one_off_outflows:
        last_outflow_year = max(fy_for_date(e.date) for e in one_off_outflows)

    last_year = max(
        retirement_date.year,
        last_goal_fy.year,
        last_outflow_year,
    )
    return min(last_year - latest_update_date.year, cap)


def _fy_label(fy_year: int) -> str:
    return f"FY{fy_year}"


def project_cashflow(
    ctx: RunContext,
    existing_mortgages: list[MortgageSchedule],
    goal_mortgages: list[MortgageSchedule],
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
    horizon_years: int,
    warnings: list[str],
) -> tuple[list[MonthlyCashflowRow], list[AnnualCashflowRow]]:
    """Project monthly + annual cashflows over the horizon.

    Annual rows step income/expense/investment by their growth/inflation factors.
    Monthly rows divide annual fields by 12 (so the monthly column always shows
    the annualized monthly equivalent). savings_2_avg is the FY's annual savings_2
    divided by the *count of months in that FY* (partial first FY → fewer months).
    nfa_opening/roi/closing are zeroed here; the funding stage computes real NFA.
    """
    annual: list[AnnualCashflowRow] = []
    for i in range(horizon_years + 1):
        fy_year = ctx.current_fy_year + i
        fy_end = date(fy_year, 3, 31)

        income_annual = ctx.annual_income * (1 + ctx.annual_income_growth) ** i
        tax_annual = income_annual * ctx.tax_rate
        expense_annual = ctx.annual_household_expense * (1 + ctx.inflation_household_expense) ** i
        existing_emi = sum(s.total_emi_in_fy(fy_end) for s in existing_mortgages)
        goal_emi = sum(s.total_emi_in_fy(fy_end) for s in goal_mortgages)
        savings_1_annual = income_annual - tax_annual - expense_annual
        savings_2_annual = savings_1_annual - existing_emi - goal_emi
        one_off_in = sum(e.amount for e in one_off_inflows if fy_for_date(e.date) == fy_year)
        one_off_out = sum(e.amount for e in one_off_outflows if fy_for_date(e.date) == fy_year)

        if ctx.monthly_investment_next_12m is not None:
            investment_annual = (
                ctx.monthly_investment_next_12m * 12
                * (1 + ctx.annual_invested_amount_growth) ** i
            )
        else:
            investment_annual = 0.0

        annual.append(AnnualCashflowRow(
            fy_end_date=fy_end,
            fy_label=_fy_label(fy_year),
            income=income_annual,
            income_tax=tax_annual,
            household_expense=expense_annual,
            savings_1=savings_1_annual,
            existing_mortgage_emi_total=existing_emi,
            goal_mortgage_emi_total=goal_emi,
            savings_2=savings_2_annual,
            one_off_in=one_off_in,
            one_off_out=one_off_out,
            investment_amount=investment_annual,
            nfa_opening=0.0,
            nfa_roi=0.0,
            nfa_closing=0.0,
        ))

    # Build monthly rows.
    monthly: list[MonthlyCashflowRow] = []
    for arow in annual:
        fy_year = int(arow.fy_label[2:])
        month_ends = _months_in_fy(fy_year, ctx.latest_update_date)
        count = len(month_ends)
        if count == 0:
            continue
        # Monthly fields = annual / 12 (the canonical monthly equivalent).
        # savings_2_avg = annual savings_2 / count_of_months_in_FY.
        savings_2_avg = arow.savings_2 / count
        monthly_income = arow.income / 12
        monthly_tax = arow.income_tax / 12
        monthly_expense = arow.household_expense / 12
        monthly_existing_emi = arow.existing_mortgage_emi_total / count if count else 0.0
        monthly_goal_emi = arow.goal_mortgage_emi_total / count if count else 0.0
        monthly_savings_1 = monthly_income - monthly_tax - monthly_expense
        monthly_savings_2 = monthly_savings_1 - monthly_existing_emi - monthly_goal_emi

        for me in month_ends:
            monthly.append(MonthlyCashflowRow(
                month_end_date=me,
                fy_label=arow.fy_label,
                income=monthly_income,
                income_tax=monthly_tax,
                household_expense=monthly_expense,
                savings_1=monthly_savings_1,
                existing_mortgage_emi_total=monthly_existing_emi,
                goal_mortgage_emi_total=monthly_goal_emi,
                savings_2=monthly_savings_2,
                savings_2_avg=savings_2_avg,
            ))

    return monthly, annual


def _months_in_fy(fy_year: int, latest_update_date: date) -> list[date]:
    """List of month-end dates within a given FY, starting from latest_update_date's month
    if this is the first (partial) FY, otherwise April→March."""
    fy_end = date(fy_year, 3, 31)
    fy_start = date(fy_year - 1, 4, 1)

    # First month: max(latest_update_date's month-start, fy_start)
    if latest_update_date >= fy_start and latest_update_date <= fy_end:
        # Partial first FY: start from latest_update_date's month
        first_month_start = date(latest_update_date.year, latest_update_date.month, 1)
    else:
        first_month_start = fy_start

    out: list[date] = []
    i = 0
    while True:
        # eomonth(d, k) returns the EOM of (d.month + k). With first_month_start.day=1,
        # this gives a clean month walker: i=0 → EOM of first month, etc.
        me = eomonth(first_month_start, i)
        if me > fy_end:
            break
        out.append(me)
        i += 1
    return out
