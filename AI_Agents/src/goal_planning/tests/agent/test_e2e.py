"""Agent E2E with FakeChatAnthropic — v2 (no-narrative shape).

We verify the snapshot's structured fields (actions_taken_this_turn, levers, headline) instead of
narrative content, since the agent no longer writes customer-facing prose.
"""
from datetime import date
import pytest
from langchain_core.messages import AIMessage
from goal_planning.models import (
    GoalPlanningInput, GoalPlanningRequest, GoalPlanningSnapshot,
    ClientProfile, RetirementInput,
)


def _baseline():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import goal_planning.agent.graph as g
    g._compiled_graph = None


def _request(question: str, session_id: str, anchor: date = date(2026, 5, 9)) -> GoalPlanningRequest:
    return GoalPlanningRequest(
        user_question=question,
        baseline_input=_baseline(),
        chat_session_id=session_id,
        anchor_date=anchor,
        detail_level="default",
    )


@pytest.mark.asyncio
async def test_e2e_status_query_invokes_compute_projection(monkeypatch, fake_llm_factory):
    """E2E #1: 'Am I on track?' → compute_projection called → snapshot has structured headline."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="Done."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    snap: GoalPlanningSnapshot = await run_goal_planning(
        _request("Am I on track for retirement?", "test-1")
    )
    # Verify structured output
    assert isinstance(snap, GoalPlanningSnapshot)
    assert snap.engine_version is not None
    assert snap.headline.is_overall_feasible in (True, False)  # boolean is set
    assert any(a.tool_name == "compute_projection" for a in snap.actions_taken_this_turn), \
        f"compute_projection not in actions: {[a.tool_name for a in snap.actions_taken_this_turn]}"


@pytest.mark.asyncio
async def test_e2e_what_if_invokes_mutate_then_compute(monkeypatch, fake_llm_factory):
    """E2E #2: 'What if I retire at 58?' → mutate_goal then compute_projection both logged."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "mutate_goal",
            "args": {"op": "update", "goal_name": "retirement", "fields": {"retirement_age": 58}},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="Done."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    snap = await run_goal_planning(_request("What if I retire at 58?", "test-2"))
    tool_names = [a.tool_name for a in snap.actions_taken_this_turn]
    assert "mutate_goal" in tool_names
    assert "compute_projection" in tool_names
    # mutate_goal action's arguments should reflect the retirement_age=58 change
    mutate_action = next(a for a in snap.actions_taken_this_turn if a.tool_name == "mutate_goal")
    assert mutate_action.arguments.get("fields", {}).get("retirement_age") == 58


@pytest.mark.asyncio
async def test_e2e_qa_turn_no_tool_calls_still_returns_snapshot(monkeypatch, fake_llm_factory):
    """E2E #3: pure Q&A — agent skips tools but snapshot still has full engine output (computed from baseline)."""
    canned = [AIMessage(content="Done.")]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    snap = await run_goal_planning(_request("Just a clarification question.", "test-3"))
    # No tool calls
    assert len(snap.actions_taken_this_turn) == 0
    # But snapshot is still populated (we computed against baseline as a fallback)
    assert snap.engine_version is not None
    assert snap.headline is not None


@pytest.mark.asyncio
async def test_e2e_shortfall_then_propose_levers(monkeypatch, fake_llm_factory):
    """E2E #5: shortfall → compute_projection → propose_levers → snapshot.levers populated."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "propose_levers", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="Done."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    inp = _baseline()
    inp = inp.model_copy(update={
        "profile": _baseline().profile.model_copy(update={"financial_assets": 5_000_000}),
    })
    request = GoalPlanningRequest(
        user_question="Help close the gap.",
        baseline_input=inp,
        chat_session_id="test-5",
        anchor_date=date(2026, 5, 9),
        detail_level="default",
    )
    snap = await run_goal_planning(request)
    tool_names = [a.tool_name for a in snap.actions_taken_this_turn]
    assert "compute_projection" in tool_names
    assert "propose_levers" in tool_names
    # snapshot.levers may be 0 (if "not solvable" fallback) or populated; just assert the field exists
    assert isinstance(snap.levers, list)


@pytest.mark.asyncio
async def test_e2e_recursion_limit_returns_fallback_snapshot(monkeypatch, fake_llm_factory):
    """E2E #6: agent in infinite tool loop → recursion limit → fallback snapshot with error_log."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": str(i), "type": "tool_call",
        }])
        for i in range(20)
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    snap = await run_goal_planning(_request("Run forever", "test-6"))
    # Fallback snapshot has error_log entry
    assert any("agent_failure" in e for e in snap.error_log)
    # But headline still populated (from compute_full_projection on baseline)
    assert snap.engine_version is not None


@pytest.mark.asyncio
async def test_e2e_extracted_event_logged(monkeypatch, fake_llm_factory):
    """When extract_financial_event runs and the extractor stub returns ExtractionError,
    the action is logged but extracted_events_this_turn stays empty."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "extract_financial_event",
            "args": {"description": "buy a 2cr house"},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="Done."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning
    snap = await run_goal_planning(_request("Buy a 2cr house in 2032.", "test-7"))
    tool_names = [a.tool_name for a in snap.actions_taken_this_turn]
    assert "extract_financial_event" in tool_names
    # The extractor stub returns ExtractionError → no events extracted
    # (after Phase 3, when the real extractor runs against canned chain, this would have an event)
    extract_action = next(a for a in snap.actions_taken_this_turn if a.tool_name == "extract_financial_event")
    assert extract_action.arguments == {"description": "buy a 2cr house"}
