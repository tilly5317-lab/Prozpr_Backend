"""Pre-compute derived insights so the responder doesn't have to scan arrays."""
from __future__ import annotations
from datetime import date

from goal_planning.models import (
    DerivedStats, GoalCategoryAggregate, GoalFundingStatus,
    AnnualCashflowRow, MonthlyNFARow, MortgageAmortization, RetirementSnapshot,
)


def compute_derived_stats(
    *,
    nfa_monthly_series: list[MonthlyNFARow],          # γ data — engine ALWAYS computes this internally
    annual_cashflow: list[AnnualCashflowRow],
    mortgage_amortizations: list[MortgageAmortization],
    goals: list[GoalFundingStatus],
    retirement: RetirementSnapshot,
    closing_nfa: float,
    inflation_household_expense: float,
    horizon_years: int,
) -> DerivedStats:
    """Single-pass scans over engine intermediates → DerivedStats."""

    # NFA timeline highlights — gracefully handle the already-retired / empty-horizon edge case
    if nfa_monthly_series:
        peak_idx = max(range(len(nfa_monthly_series)), key=lambda i: nfa_monthly_series[i].nfa_close)
        min_idx = min(range(len(nfa_monthly_series)), key=lambda i: nfa_monthly_series[i].nfa_close)
        peak_nfa_amount = nfa_monthly_series[peak_idx].nfa_close
        peak_nfa_date = nfa_monthly_series[peak_idx].month_end
        min_nfa_amount = nfa_monthly_series[min_idx].nfa_close
        min_nfa_date = nfa_monthly_series[min_idx].month_end
    else:
        # Already retired or empty horizon — fall back to closing_nfa as a single point.
        peak_nfa_amount = closing_nfa
        peak_nfa_date = retirement.retirement_date
        min_nfa_amount = closing_nfa
        min_nfa_date = retirement.retirement_date

    # NFA at retirement: find row where month_end matches the FY of retirement_date
    nfa_at_retirement: float | None = None
    if retirement.years_to_retirement > 0 and retirement.retirement_date:
        # Find the closest month_end <= retirement_date
        target = retirement.retirement_date
        candidates = [r for r in nfa_monthly_series if r.month_end <= target]
        if candidates:
            nfa_at_retirement = candidates[-1].nfa_close

    # Closing NFA in today's money
    closing_nfa_pv = (
        closing_nfa / ((1 + inflation_household_expense) ** horizon_years)
        if horizon_years > 0
        else closing_nfa
    )

    # Cashflow highlights — annual_cashflow has at least 1 row (current FY) by construction
    if annual_cashflow:
        worst_idx = min(range(len(annual_cashflow)), key=lambda i: annual_cashflow[i].savings_2)
        best_idx = max(range(len(annual_cashflow)), key=lambda i: annual_cashflow[i].savings_2)
        worst_savings_fy = annual_cashflow[worst_idx].fy_label
        worst_savings_amount = annual_cashflow[worst_idx].savings_2
        best_savings_fy = annual_cashflow[best_idx].fy_label
        best_savings_amount = annual_cashflow[best_idx].savings_2
    else:
        worst_savings_fy = ""
        worst_savings_amount = 0.0
        best_savings_fy = ""
        best_savings_amount = 0.0

    # Debt-free date: earliest month where ALL mortgage closing_balance == 0.
    # Pragmatic: this is the latest payoff_date across all mortgages — the moment
    # the last one closes is when ALL are paid off (mortgages with later start dates
    # have later end dates by construction).
    debt_free_date: date | None = None
    if mortgage_amortizations:
        latest_payoff = max(
            (sched.monthly_schedule[-1].month_end for sched in mortgage_amortizations if sched.monthly_schedule),
            default=None,
        )
        debt_free_date = latest_payoff

    # Goals by category
    cats: dict[str, list[GoalFundingStatus]] = {}
    for g in goals:
        cats.setdefault(g.goal_type.value, []).append(g)
    goals_by_category: dict[str, GoalCategoryAggregate] = {}
    for cat, goals_in_cat in cats.items():
        total_amount_pv = sum(g.amount_pv for g in goals_in_cat)
        total_amount_fv = sum(g.amount_fv for g in goals_in_cat)
        total_funded = sum(g.funded_amount for g in goals_in_cat)
        total_shortfall = sum(g.shortfall_fv for g in goals_in_cat)
        all_funded = all(g.is_funded for g in goals_in_cat)
        goals_by_category[cat] = GoalCategoryAggregate(
            count=len(goals_in_cat),
            total_amount_pv=total_amount_pv,
            total_amount_fv=total_amount_fv,
            total_funded=total_funded,
            total_shortfall=total_shortfall,
            all_funded=all_funded,
        )

    # Retirement runway: months until NFA hits 0 post-retirement
    months_corpus_will_last_post_retirement: int | None = None
    if retirement.retirement_date and retirement.years_to_retirement > 0:
        post_retirement = [r for r in nfa_monthly_series if r.month_end > retirement.retirement_date]
        # Find first month where nfa_close <= 0
        for i, row in enumerate(post_retirement):
            if row.nfa_close <= 0:
                months_corpus_will_last_post_retirement = i
                break

    return DerivedStats(
        peak_nfa_amount=peak_nfa_amount,
        peak_nfa_date=peak_nfa_date,
        min_nfa_amount=min_nfa_amount,
        min_nfa_date=min_nfa_date,
        nfa_at_retirement=nfa_at_retirement,
        closing_nfa_pv=closing_nfa_pv,
        worst_savings_fy=worst_savings_fy,
        worst_savings_amount=worst_savings_amount,
        best_savings_fy=best_savings_fy,
        best_savings_amount=best_savings_amount,
        debt_free_date=debt_free_date,
        goals_by_category=goals_by_category,
        months_corpus_will_last_post_retirement=months_corpus_will_last_post_retirement,
    )
