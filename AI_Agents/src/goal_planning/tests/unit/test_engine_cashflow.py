from datetime import date
import pytest
from goal_planning.models import Assumptions, ClientProfile, OneOffEvent
from goal_planning.engine.profile import build_initial_context
from goal_planning.engine.cashflow import project_cashflow, compute_horizon_years


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        Assumptions(),
    )


def test_savings_1_first_month():
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    first = monthly[0]
    # income/12 = 166,667; tax = 30% = 50,000; expense = 80,000; savings_1 = 36,667
    assert first.savings_1 == pytest.approx(166_666.67 - 50_000 - 80_000, rel=1e-3)


def test_savings_2_subtracts_emi():
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    first = monthly[0]
    assert first.savings_2 == pytest.approx(first.savings_1, rel=1e-9)


def test_income_step_up_year_2():
    ctx = _ctx()
    _, annual = project_cashflow(ctx, [], [], [], [], horizon_years=3, warnings=[])
    assert annual[0].income == pytest.approx(2_000_000, rel=1e-3)
    assert annual[1].income == pytest.approx(2_000_000 * 1.08, rel=1e-3)
    assert annual[2].income == pytest.approx(2_000_000 * 1.08 ** 2, rel=1e-3)


def test_expense_step_up_per_fy():
    ctx = _ctx()
    _, annual = project_cashflow(ctx, [], [], [], [], horizon_years=3, warnings=[])
    base = 80_000 * 12
    assert annual[1].household_expense == pytest.approx(base * 1.06, rel=1e-3)


def test_savings_2_avg_constant_within_fy():
    """savings_2_avg is the FY-bucket average — same value across all months in same FY."""
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    fy_groups: dict[str, list[float]] = {}
    for r in monthly:
        fy_groups.setdefault(r.fy_label, []).append(r.savings_2_avg)
    for fy, values in fy_groups.items():
        assert len(set(values)) == 1, f"savings_2_avg should be constant within {fy}"


def test_horizon_years_includes_one_off_outflows():
    horizon = compute_horizon_years(
        retirement_date=date(2036, 5, 9),
        last_goal_fy=date(2040, 3, 31),
        one_off_outflows=[OneOffEvent(description="x", amount=1_000_000, date=date(2050, 6, 1))],
        latest_update_date=date(2026, 5, 9),
        cap=80,
    )
    # 2050-FY = 2051; horizon = 2051 - 2026 = 25 years
    assert horizon == 25


def test_horizon_capped_at_80():
    horizon = compute_horizon_years(
        retirement_date=date(2200, 1, 1),
        last_goal_fy=date(2200, 3, 31),
        one_off_outflows=[],
        latest_update_date=date(2026, 5, 9),
        cap=80,
    )
    assert horizon == 80
