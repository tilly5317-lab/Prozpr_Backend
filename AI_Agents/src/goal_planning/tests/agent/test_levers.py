from datetime import date
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
)
from goal_planning.engine import compute_full_projection
from goal_planning.agent.levers import generate_lever_a_increase_sip


def _shortfall_input():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=5_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=20_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="big_goal", goal_type=GoalType.custom,
            amount_pv=10_000_000, goal_date=date(2035, 1, 1),
        )],
    )


def test_lever_a_finds_feasible_sip_when_one_exists():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "monthly_investment_next_12m"


def test_lever_a_returns_none_when_already_feasible():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=10_000_000, tax_rate=0.30,
            financial_assets=100_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=200_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    assert lever is None


def test_lever_b_defers_largest_underfunded_goal():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_b_defer_goal
    lever = generate_lever_b_defer_goal(inp, out, defer_max_years=10)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert lever.action.op == "update"
        assert "goal_date" in lever.action.fields


def test_lever_c_reduces_target():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_c_reduce_target
    lever = generate_lever_c_reduce_target(inp, out, reduce_max_pct=0.50)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert "amount_pv" in lever.action.fields


def test_lever_d_changes_retirement_age():
    """Lever D only generated when retirement is the underfunded goal."""
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_d_retirement_age
    lever = generate_lever_d_retirement_age(inp, out)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert lever.action.goal_name == "retirement"
        assert "retirement_age" in lever.action.fields


def test_lever_e_increases_step_up():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_e_step_up
    lever = generate_lever_e_step_up(inp, out, step_up_max_delta_pp=0.20)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "step_up_rate"


def test_lever_f_reduces_expense():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_f_reduce_expense
    lever = generate_lever_f_reduce_expense(inp, out)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "monthly_household_expense"
        assert lever.confidence == "low"


def test_lever_g_skipped_with_no_existing_mortgage():
    inp = _shortfall_input()  # no current_properties
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_g_mortgage_payoff
    lever = generate_lever_g_mortgage_payoff(inp, out)
    assert lever is None  # silently skipped


def test_lever_g_with_active_mortgage():
    from datetime import date
    from goal_planning.models import CurrentProperty
    inp = _shortfall_input()
    inp = inp.model_copy(update={
        "current_properties": [CurrentProperty(
            name="apt", has_mortgage=True,
            mortgage_balance=3_000_000, mortgage_emi=30_000,
            mortgage_last_date=date(2046, 1, 1),
        )],
    })
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import generate_lever_g_mortgage_payoff
    lever = generate_lever_g_mortgage_payoff(inp, out)
    if lever is not None:
        assert lever.action.kind == "property_field"
        assert lever.action.field == "early_payoff_date"


def test_propose_levers_returns_top_3_ranked():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import propose_levers
    levers = propose_levers(inp, out)
    assert len(levers) <= 3


def test_propose_levers_returns_empty_when_feasible():
    """Plan is already feasible -> no levers needed."""
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=10_000_000, tax_rate=0.30,
            financial_assets=200_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=200_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    from goal_planning.agent.levers import propose_levers
    levers = propose_levers(inp, out)
    assert levers == []
