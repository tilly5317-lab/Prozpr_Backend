"""Stage 6: cashflow projection — monthly + annual rows with FY step-ups."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import (
    AnnualCashflowRow, MonthlyCashflowRow, OneOffEvent,
)
from cashflow_statement.engine._types import RunContext, MortgageSchedule
from cashflow_statement.engine.dates import fy_for_date, eomonth


def compute_horizon_years(
    retirement_date: date,
    latest_update_date: date,
    cap: int = 80,
) -> int:
    """Horizon in FY years — projection ends at the retirement FY.

    Rationale: the customer-facing question this engine answers is "is retirement
    feasible at the planned age?". That answer is read off corpus-vs-corpus_required
    at retirement_date. Continuing the projection past retirement only produces
    stuck-corpus noise (no income, no SIPs, the retirement corpus already paid out
    as a goal). Goals or one-off events with dates after retirement are dropped
    from the projection — pipeline.py emits a warning for any such inputs.
    """
    current_fy = fy_for_date(latest_update_date)
    retirement_fy = fy_for_date(retirement_date)
    return min(retirement_fy - current_fy, cap)


def _fy_label(fy_year: int) -> str:
    return f"FY{fy_year}"


def project_cashflow(
    ctx: RunContext,
    existing_mortgages: list[MortgageSchedule],
    goal_mortgages: list[MortgageSchedule],
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
    years_to_last_goal: int,
) -> list[MonthlyCashflowRow]:
    """Project monthly cashflow over the horizon.

    Per-month income/expense/SIP use the FY's annualised rate divided by 12.
    Mortgage EMIs are partial-FY-aware via `total_emi_in_fy` and spread evenly
    over the FY's months for display (the FY total is exact; the per-month
    distribution is uniform). One-off events land in their event-month row
    (matched by year + month). corpus evolution lives in the funding stage.

    Annual aggregates are derived from this monthly view via
    `derive_annual_cashflow` at the pipeline boundary.
    """
    # Index one-offs by (year, month) for O(1) per-row lookup.
    inflow_by_ym: dict[tuple[int, int], float] = {}
    outflow_by_ym: dict[tuple[int, int], float] = {}
    for e in one_off_inflows:
        ym = (e.date.year, e.date.month)
        inflow_by_ym[ym] = inflow_by_ym.get(ym, 0.0) + e.amount
    for e in one_off_outflows:
        ym = (e.date.year, e.date.month)
        outflow_by_ym[ym] = outflow_by_ym.get(ym, 0.0) + e.amount

    monthly: list[MonthlyCashflowRow] = []

    for i in range(years_to_last_goal + 1):
        fy_year = ctx.current_fy_year + i
        fy_end = date(fy_year, 3, 31)
        month_ends = _months_in_fy(fy_year, ctx.latest_update_date)
        count = len(month_ends)
        if count == 0:
            continue

        # Annualised pre-retire basis for this FY. Income is zeroed per-row in the
        # month loop below (not at FY level), so the retirement month itself gets
        # zero income — matching the funding stage's MONTH-level post-retire check
        # (funding.py: m > retirement_date) and preventing the row from showing
        # phantom savings that aren't actually invested.
        income_basis = ctx.annual_income * (1 + ctx.annual_income_growth) ** i
        tax_basis = income_basis * ctx.effective_tax_rate
        expense_basis = ctx.annual_household_expense * (1 + ctx.inflation_household_expense) ** i

        # Per-month values (constant within FY, before the per-row retire check).
        pre_retire_monthly_income = income_basis / 12
        pre_retire_monthly_tax = tax_basis / 12
        monthly_expense = expense_basis / 12

        # Mortgage EMIs in this FY spread evenly across the FY's display months.
        existing_emi_in_fy = sum(s.total_emi_in_fy(fy_end) for s in existing_mortgages)
        goal_emi_in_fy = sum(s.total_emi_in_fy(fy_end) for s in goal_mortgages)
        monthly_existing_emi = existing_emi_in_fy / count
        monthly_goal_emi = goal_emi_in_fy / count

        retirement_date = ctx.retirement_date_considered

        for me in month_ends:
            ym = (me.year, me.month)
            # Post-retirement, household has no salary income. Check is
            # MONTH-level so the retirement month itself reflects zero income — the
            # corpus payout at retirement_date funds post-retirement expenses in our
            # lump-sum model. Aligns with funding.py's M147 retirement check.
            #
            # household_expense is intentionally NOT zeroed here for post-retirement
            # rows. This loop generates a full FY of rows (including months after
            # retirement_date in the retirement FY), but pipeline.py truncates those
            # rows before funding sees them — so the post-retirement expense values
            # are dead data, never observed downstream.
            is_post_retire = retirement_date is not None and me > retirement_date
            monthly_income = 0.0 if is_post_retire else pre_retire_monthly_income
            monthly_tax = 0.0 if is_post_retire else pre_retire_monthly_tax
            monthly_savings_pre_emi = monthly_income - monthly_tax - monthly_expense
            monthly_savings_post_emi = (
                monthly_savings_pre_emi - monthly_existing_emi - monthly_goal_emi
            )
            monthly.append(MonthlyCashflowRow(
                month_end_date=me,
                fy_label=_fy_label(fy_year),
                income=monthly_income,
                income_tax=monthly_tax,
                household_expense=monthly_expense,
                savings_pre_emi=monthly_savings_pre_emi,
                existing_mortgage_emi=monthly_existing_emi,
                goal_mortgage_emi=monthly_goal_emi,
                savings_post_emi=monthly_savings_post_emi,
                one_off_inflow=inflow_by_ym.get(ym, 0.0),
                one_off_outflow=outflow_by_ym.get(ym, 0.0),
            ))

    return monthly


def derive_annual_cashflow(monthly: list[MonthlyCashflowRow]) -> list[AnnualCashflowRow]:
    """Aggregate monthly rows into per-FY rows.

    P&L columns (income through one_off_outflow) are pure column sums. corpus columns
    (corpus_opening / monthly_investment / roi / goal_payout / corpus_closing / is_funded)
    are aggregated naturally:
      - corpus_opening   = the FY's first row's opening balance
      - corpus_closing  = the FY's last row's closing balance
      - monthly_investment / roi / goal_payout = column sums
      - is_funded = True only if every month in the FY was funded

    Callers that pass un-enriched monthly rows (corpus fields at their defaults of
    0.0 / True) get correctly-zero corpus aggregates — useful for unit tests that
    only exercise the P&L stage without running funding.
    """
    by_fy: dict[str, list[MonthlyCashflowRow]] = {}
    for r in monthly:
        by_fy.setdefault(r.fy_label, []).append(r)

    annual: list[AnnualCashflowRow] = []
    for fy_label, rows in by_fy.items():
        fy_year = int(fy_label[2:])  # "FY2027" → 2027
        annual.append(AnnualCashflowRow(
            fy_end_date=date(fy_year, 3, 31),
            fy_label=fy_label,
            # P&L sums
            income=sum(r.income for r in rows),
            income_tax=sum(r.income_tax for r in rows),
            household_expense=sum(r.household_expense for r in rows),
            savings_pre_emi=sum(r.savings_pre_emi for r in rows),
            existing_mortgage_emi=sum(r.existing_mortgage_emi for r in rows),
            goal_mortgage_emi=sum(r.goal_mortgage_emi for r in rows),
            savings_post_emi=sum(r.savings_post_emi for r in rows),
            one_off_inflow=sum(r.one_off_inflow for r in rows),
            one_off_outflow=sum(r.one_off_outflow for r in rows),
            # corpus evolution — open is first row, close is last row, others are sums.
            corpus_opening=rows[0].corpus_opening,
            monthly_investment=sum(r.monthly_investment for r in rows),
            investment_returns=sum(r.investment_returns for r in rows),
            goal_payout=sum(r.goal_payout for r in rows),
            corpus_closing=rows[-1].corpus_closing,
            is_funded=all(r.is_funded for r in rows),
        ))
    return annual


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
