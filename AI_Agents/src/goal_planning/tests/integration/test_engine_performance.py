"""Performance + memory budgets per spec §10.7."""
import time
import tracemalloc
from datetime import date
import pytest

from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
)
from goal_planning.engine import compute_full_projection


def _realistic_input():
    """Indian-realistic: NFA 5Cr, income 25L, 21 goals, 50-year horizon."""
    custom_goals = [
        CustomGoal(
            name=f"goal_{i}", goal_type=GoalType.custom,
            amount_pv=5_000_000, goal_date=date(2030 + i, 5, 9),
        )
        for i in range(20)
    ]
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_500_000, tax_rate=0.30,
            financial_assets=50_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=120_000, monthly_investment_next_12m=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=custom_goals,
    )


def test_engine_call_under_500ms():
    inp = _realistic_input()
    # Warmup once (Python import + first compile)
    compute_full_projection(inp)
    # Measure
    start = time.perf_counter()
    out = compute_full_projection(inp)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"Engine too slow: {elapsed*1000:.0f}ms"


def test_engine_memory_under_50mb():
    inp = _realistic_input()
    tracemalloc.start()
    out = compute_full_projection(inp)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 50 * 1024 * 1024, f"Engine peak memory: {peak/1024/1024:.1f}MB"
