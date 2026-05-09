from datetime import date
import pytest

from goal_planning.agent.tools import (
    extract_financial_event_impl,
    apply_override_impl,
    clear_overrides_impl,
    mutate_goal_impl,
    compute_projection_impl,
    propose_levers_impl,
)
from goal_planning.agent.state import AgentState
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, NumericOverride,
)


def _state() -> AgentState:
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    return {  # type: ignore[return-value]
        "messages": [],
        "baseline_input": inp,
        "anchor_date": date(2026, 5, 9),
        "accumulated_overrides": [],
        "captured_goals": [],
        "captured_properties": [],
        "captured_cashflows": [],
        "captured_mutations": [],
        "last_output": None,
        "last_levers": [],
        "dirty": False,
        "error_log": [],
    }


def test_apply_override_appends_to_state():
    state = _state()
    summary = apply_override_impl(
        NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000), state,
    )
    assert len(state["accumulated_overrides"]) == 1
    assert state["dirty"] is True
    assert "monthly_investment_next_12m" in summary


def test_clear_overrides_empties_state():
    state = _state()
    state["accumulated_overrides"] = [
        NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000),
    ]
    summary = clear_overrides_impl(None, state)
    assert state["accumulated_overrides"] == []
    assert "cleared" in summary.lower()


@pytest.mark.asyncio
async def test_extract_financial_event_with_stub_returns_error():
    state = _state()
    summary = await extract_financial_event_impl("buy a house", state)
    assert "not yet implemented" in summary.lower() or "could not" in summary.lower()


def test_mutate_goal_appends():
    state = _state()
    summary = mutate_goal_impl("update", "retirement", {"retirement_age": 58}, state)
    assert len(state["captured_mutations"]) == 1
    assert state["dirty"] is True


def test_compute_projection_runs_when_dirty():
    state = _state()
    state["dirty"] = True
    summary = compute_projection_impl(state)
    assert state["last_output"] is not None
    assert state["dirty"] is False  # reset post-compute


def test_compute_projection_short_circuits_when_clean():
    state = _state()
    state["dirty"] = True
    compute_projection_impl(state)  # populate last_output
    state["dirty"] = False
    summary = compute_projection_impl(state)
    # Should return cached summary
    assert "cached" in summary.lower() or "feasible" in summary.lower()


def test_propose_levers_no_op_when_no_output():
    state = _state()
    summary = propose_levers_impl(state)
    assert "compute_projection first" in summary.lower() or "first" in summary.lower()
