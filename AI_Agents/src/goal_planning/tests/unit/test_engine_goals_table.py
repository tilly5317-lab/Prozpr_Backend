from datetime import date
import pytest
from goal_planning.models import (
    Assumptions, ClientProfile, RetirementInput, CustomGoal, GoalType, RetirementSnapshot,
)
from goal_planning.engine.profile import build_initial_context
from goal_planning.engine.goals_table import (
    expected_roi_for_goal, build_goals_table,
)


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def _retirement_snap():
    return RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )


def test_expected_roi_three_bands():
    ctx = _ctx()
    # near_term_end = 2029-03-31; medium_term_end = 2032-03-31
    assert expected_roi_for_goal(date(2027, 6, 1), ctx) == pytest.approx(0.05)
    assert expected_roi_for_goal(date(2030, 6, 1), ctx) == pytest.approx(0.07)
    assert expected_roi_for_goal(date(2040, 1, 1), ctx) == pytest.approx(0.09)


def test_retirement_uses_corpus_used_directly_skipping_inflation_lookup():
    ctx = _ctx()
    goals = build_goals_table(_retirement_snap(), [], [], ctx, Assumptions(), [])
    retirement = next(g for g in goals if g.goal_type == GoalType.retirement)
    assert retirement.amount_fv == 30_000_000
    assert retirement.inflation_rate == ctx.inflation_household_expense


def test_custom_goal_amount_fv_inflated_by_goal_type_default():
    ctx = _ctx()
    goals = build_goals_table(
        _retirement_snap(), [],
        [CustomGoal(
            name="college", goal_type=GoalType.child_local_education,
            amount_pv=1_000_000, goal_date=date(2035, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    college = next(g for g in goals if g.name == "college")
    assert college.inflation_rate == 0.06
    assert 1_500_000 < college.amount_fv < 1_800_000


def test_amount_fv_when_user_provides_fv_directly():
    ctx = _ctx()
    goals = build_goals_table(
        _retirement_snap(), [],
        [CustomGoal(
            name="abroad_ed", goal_type=GoalType.child_abroad_education,
            amount_fv=20_000_000, goal_date=date(2040, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    g = next(g for g in goals if g.name == "abroad_ed")
    assert g.amount_fv == pytest.approx(20_000_000, rel=1e-6)


def test_fund_today_pv_discount():
    ctx = _ctx()
    goals = build_goals_table(
        _retirement_snap(), [],
        [CustomGoal(
            name="g1", goal_type=GoalType.custom, amount_fv=10_000_000, goal_date=date(2040, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    g = next(g for g in goals if g.name == "g1")
    years = (date(2040, 1, 1) - ctx.latest_update_date).days / 365.25
    expected = 10_000_000 / (1.09 ** years)
    assert g.fund_today_pv == pytest.approx(expected, rel=1e-3)
