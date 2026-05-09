from datetime import date
from goal_planning.engine._types import (
    RunContext, MortgageAnnualRow, GoalPropertyOutcome, GoalInternal,
)
from goal_planning.models import GoalType, RetirementSnapshot


def test_run_context_with_retirement_immutable_update():
    ctx = RunContext(
        nfa=15_000_000, latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000, annual_household_expense=960_000,
        monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        tax_rate=0.30, current_fy_end=date(2026, 3, 31), current_fy_year=2026,
        near_term_end=date(2029, 3, 31), medium_term_end=date(2032, 3, 31),
        retirement_date_considered=None, retired_portfolio_roi_annual=0.09,
        real_roi_retired_monthly=0.0023,
        sip_share=0.75, annual_income_growth=0.08, annual_invested_amount_growth=0.08,
        inflation_household_expense=0.06, near_term_roi=0.05, mid_term_roi=0.07, long_term_roi=0.09,
    )
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    new_ctx = ctx.with_retirement(snap)
    assert ctx.retirement_date_considered is None  # original unchanged
    assert new_ctx.retirement_date_considered == date(2036, 5, 9)
    assert new_ctx.real_roi_retired_monthly == 0.0023


def test_goal_internal_construction():
    g = GoalInternal(
        name="college", goal_type=GoalType.child_local_education,
        goal_date=date(2035, 1, 1), goal_date_fy=date(2035, 3, 31),
        amount_pv=1_000_000, amount_fv=2_000_000, inflation_rate=0.06,
        expected_roi=0.07, fund_today_pv=1_500_000,
    )
    assert g.fund_today_pv == 1_500_000
