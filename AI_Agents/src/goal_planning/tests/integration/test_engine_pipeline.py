from datetime import date
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, Assumptions, CustomGoal, GoalType,
)
from goal_planning.engine.pipeline import compute_full_projection, ENGINE_VERSION


def test_minimal_pipeline_runs_end_to_end():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="college", goal_type=GoalType.child_local_education,
            amount_pv=2_000_000, goal_date=date(2035, 1, 1),
        )],
    )
    out = compute_full_projection(inp)
    assert out.engine_version == ENGINE_VERSION
    assert out.headline.number_of_goals >= 2  # retirement + college
    assert out.retirement.corpus_required_used > 0
    assert isinstance(out.headline.is_overall_feasible, bool)


def test_default_detail_level_omits_gamma_fields():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    assert out.monthly_cashflow is None
    assert out.nfa_monthly_series is None
    assert out.mortgage_amortizations is None


def test_full_detail_level_populates_gamma_fields():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        detail_level="full",
    )
    out = compute_full_projection(inp)
    assert out.monthly_cashflow is not None
    assert out.nfa_monthly_series is not None
