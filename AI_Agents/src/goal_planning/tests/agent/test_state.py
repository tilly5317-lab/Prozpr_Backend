from datetime import date
from goal_planning.agent.state import AgentState, CapturedCashflow
from goal_planning.models import OneOffEvent


def test_captured_cashflow_carries_direction():
    cc = CapturedCashflow(
        event=OneOffEvent(description="bonus", amount=500_000, date=date(2027, 1, 1)),
        direction="in",
    )
    assert cc.direction == "in"


def test_agent_state_has_required_keys():
    """AgentState is a TypedDict — validate via type_hints."""
    from typing import get_type_hints
    hints = get_type_hints(AgentState)
    for key in [
        "messages", "baseline_input", "anchor_date",
        "accumulated_overrides", "captured_goals", "captured_properties",
        "captured_cashflows", "captured_mutations",
        "last_output", "last_levers",
        "actions_taken_this_turn", "extracted_events_this_turn",   # NEW
        "dirty", "error_log",
    ]:
        assert key in hints, f"AgentState missing {key}"
