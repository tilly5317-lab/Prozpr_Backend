"""Step 2 on the waterfall. The golden fixtures have goals=[] and never exercise
step 2, so the property test here is the guard that defaults reproduce it."""

from __future__ import annotations

import random

from asset_allocation_pydantic.models import AllocationInput, Goal
from asset_allocation_pydantic.steps import step1_emergency, step2_short_term
from asset_allocation_pydantic.tables import HORIZON_BOUNDARY_MONTHS
from asset_allocation_pydantic.utils import round_to_rupee


def _inp(goals, **kw):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, total_corpus=20_000_000.0,
        monthly_household_expense=100_000, effective_tax_rate=15.0, goals=goals,
    )
    base.update(kw)
    return AllocationInput(**base)


def _old_step2(inp, remaining):
    goals = [g for g in inp.goals
             if g.time_to_goal_months < HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end]
    total = round_to_rupee(sum(g.amount_needed for g in goals))
    allocated = min(total, remaining)
    gap = total - remaining if total > remaining else None
    return total, allocated, remaining - allocated, gap


def test_defaults_reproduce_the_old_step2():
    rng = random.Random(7)
    for _ in range(3000):
        goals = [
            Goal(goal_name=f"g{i}", time_to_goal_months=rng.randint(1, 40),
                 amount_needed=float(rng.randint(1, 50_000) * rng.choice([1, 10, 100])),
                 goal_priority=rng.choice(["negotiable", "non_negotiable"]))
            for i in range(rng.randint(0, 5))
        ]
        inp = _inp(goals, months_to_fy_end=rng.randint(0, 11),
                   effective_tax_rate=rng.choice([10.0, 30.0]))
        remaining = rng.randint(0, 400_000) * 100
        out = step2_short_term.run(inp, remaining)
        total, allocated, left, gap = _old_step2(inp, remaining)
        assert out.total_goal_amount == total
        assert out.allocated_amount == allocated
        assert out.remaining_corpus == left
        got_gap = out.future_investment.future_investment_amount if out.future_investment else None
        assert got_gap == gap
        assert out.subgroup_amounts == ({out.asset_subgroup: allocated} if allocated > 0 else {})


def test_held_money_used_is_capped_at_the_corpus_step1_leaves():
    """10L held in debt against a 7L corpus: only 7L can fund the 9L goal."""
    goals = [Goal(goal_name="Car", time_to_goal_months=12, amount_needed=900_000.0,
                  goal_priority="non_negotiable")]
    inp = _inp(goals, total_corpus=700_000.0, short_term_holdings=1_000_000.0,
               emergency_fund_needed=False)
    funding = step2_short_term.run(inp, step1_emergency.run(inp).remaining_corpus).goal_funding
    assert (funding.allocated_amount, funding.shortfall) == (700_000, 200_000)
    car = funding.goals[0]
    assert (car.from_holdings, car.from_corpus, car.shortfall) == (700_000, 0, 200_000)


def _car_input():
    goals = [Goal(goal_name="Car", time_to_goal_months=12, amount_needed=600_000.0,
                  goal_priority="non_negotiable")]
    return _inp(goals, monthly_sip=30_000.0, short_term_holdings=100_000.0)


def test_both_engines_surface_the_same_goal_funding():
    from asset_allocation_pydantic.pipeline import run_allocation
    from practical_asset_allocation.pipeline import (
        PracticalAllocationInput,
        run_practical_allocation,
    )

    inp = _car_input()
    ideal = run_allocation(inp)
    assert ideal.goal_funding.allocated_amount == 240_000
    assert ideal.goal_funding.monthly_sip_to_goals == 30_000
    practical = run_practical_allocation(
        PracticalAllocationInput(**inp.model_dump())
    )
    assert practical.goal_funding == ideal.goal_funding


def test_preference_run_has_no_goal_funding():
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import (
        PracticalAllocationInput,
        run_practical_allocation,
    )

    inp = _car_input()
    practical = run_practical_allocation(
        PracticalAllocationInput(
            **inp.model_dump(),
            human_override=HumanOverridePreferences(
                asset_class_requested={"equity": 60.0, "debt": 35.0, "others": 5.0}
            ),
        )
    )
    assert practical.goal_funding is None


def test_shortfall_message_does_not_assume_a_monthly_investment():
    car = Goal(goal_name="Car", time_to_goal_months=12, amount_needed=600_000,
               goal_priority="non_negotiable", amount_needed_fv=600_000)
    out = step2_short_term.run(_inp([car], total_corpus=100_000.0), remaining_corpus=100_000)
    assert "monthly investment" not in out.future_investment.message
    assert "investing more each month" in out.future_investment.message
