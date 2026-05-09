from datetime import date
import pytest
from goal_planning.engine.funding import monthly_invest_or_withdraw


def test_branch_zero_post_retirement():
    """year > retirement_year → invested = 0, kind='zero'."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2040, 5, 31),
        savings_2_avg=50_000,
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert invested == 0
    assert kind == "zero"


def test_branch_user_sip_pre_retirement():
    """year < retire_year, user_sip set & > 100 → user_sip × growth^(yr - base)."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2030, 5, 31),
        savings_2_avg=200_000,
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "user_sip"
    assert invested > 50_000  # stepped up


def test_branch_savings_sip_fraction_year_equal_or_no_user_sip():
    """retire-year-equal OR user_sip None: K-based fallback. K>0 → K × sip_share."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2036, 6, 30),  # in FY 2037 = retirement-year FY
        savings_2_avg=80_000,
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "savings_sip_fraction"
    assert invested == pytest.approx(60_000, rel=1e-9)  # 80k × 0.75


def test_branch_withdrawal_negative_savings():
    """K-based fallback path, K<0 → invested = K (withdrawal)."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2030, 5, 31),
        savings_2_avg=-30_000,
        user_sip=None,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "withdrawal"
    assert invested == -30_000


def test_compute_funding_proportional_split_two_equal_goals():
    """NFA = 5M, two goals each needing 5M same date → each underfunded by ~half."""
    from datetime import date
    from goal_planning.models import Assumptions, ClientProfile, RetirementInput, CustomGoal, GoalType
    from goal_planning.engine.profile import build_initial_context
    from goal_planning.engine.retirement import compute_retirement_snapshot
    from goal_planning.engine.goals_table import build_goals_table
    from goal_planning.engine.cashflow import project_cashflow
    from goal_planning.engine.funding import compute_funding

    ctx0 = build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=0, tax_rate=0.30,
            financial_assets=5_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=0,
        ),
        Assumptions(),
    )
    snap = compute_retirement_snapshot(RetirementInput(date_of_birth=date(1996, 5, 9)), ctx0, [])
    ctx = ctx0.with_retirement(snap)
    goals = build_goals_table(
        snap, [],
        [
            CustomGoal(name="g1", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
            CustomGoal(name="g2", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
        ],
        ctx, Assumptions(), [],
    )
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    funding = compute_funding(goals, ctx, monthly, [], [], [])
    s1 = next(s for s in funding.per_goal_status if s.name == "g1")
    s2 = next(s for s in funding.per_goal_status if s.name == "g2")
    assert s1.shortfall_fv > 0
    assert s2.shortfall_fv > 0
    # Proportional → roughly equal shortfalls
    assert s1.shortfall_fv == pytest.approx(s2.shortfall_fv, rel=0.05)
