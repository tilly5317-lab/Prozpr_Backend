"""Agent E2E with FakeChatAnthropic. Six scenarios per spec §10.4."""
from datetime import date
import pytest
from langchain_core.messages import AIMessage
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput,
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
    """Helper: patch ChatAnthropic in nodes module + reset singleton graph."""
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import goal_planning.agent.graph as g
    g._compiled_graph = None


@pytest.mark.asyncio
async def test_e2e_initial_query_compute_then_narrate(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #1: User asks 'am I on track?' → agent calls compute_projection → narrate."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="Your retirement is on track. NFA today: ₹15M. No shortfalls."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    response = await run_goal_planning_agent(
        user_message="Am I on track for retirement?",
        baseline_input=_baseline(),
        chat_session_id="test-1",
        anchor_date=anchor_date,
    )
    assert "track" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_what_if_retire_at_58(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #2: 'What if I retire at 58?' → mutate_goal → compute → narrate."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "mutate_goal",
            "args": {"op": "update", "goal_name": "retirement", "fields": {"retirement_age": 58}},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="Retiring at 58 makes you underfunded by approximately ₹X."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    response = await run_goal_planning_agent(
        user_message="What if I retire at 58?",
        baseline_input=_baseline(),
        chat_session_id="test-2",
        anchor_date=anchor_date,
    )
    assert "58" in response.narrative


@pytest.mark.asyncio
async def test_e2e_q_and_a_uses_cached_output(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #4: Q&A turn — agent doesn't call any tool, narrates from prior context."""
    canned = [
        AIMessage(content="Your retirement corpus needs ₹3 Cr in today's money."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    response = await run_goal_planning_agent(
        user_message="Why is retirement tricky?",
        baseline_input=_baseline(),
        chat_session_id="test-3",
        anchor_date=anchor_date,
    )
    # No tool was called → output is None for this turn
    assert response.output is None
    assert "corpus" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_nl_goal_capture_with_stub(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #3: NL goal capture — extractor stub returns ExtractionError; agent narrates that."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "extract_financial_event",
            "args": {"description": "send daughter abroad in 2040"},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="I couldn't parse that goal yet — please provide more structured input."),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    response = await run_goal_planning_agent(
        user_message="Send daughter abroad in 2040, ~1Cr in today's money",
        baseline_input=_baseline(),
        chat_session_id="test-4",
        anchor_date=anchor_date,
    )
    assert "couldn't" in response.narrative.lower() or "structured" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_shortfall_then_propose_levers(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #5: shortfall → compute_projection → propose_levers → narrate."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "propose_levers", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="You're short. Top fixes:\n- Increase SIP\n- Defer goal by 2y"),
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    inp = _baseline()
    inp = inp.model_copy(update={
        "profile": _baseline().profile.model_copy(update={"financial_assets": 5_000_000}),
    })
    response = await run_goal_planning_agent(
        user_message="Help me with shortfall",
        baseline_input=inp,
        chat_session_id="test-5",
        anchor_date=anchor_date,
    )
    assert "fix" in response.narrative.lower() or "lever" in response.narrative.lower() or "sip" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_recursion_limit_returns_graceful_message(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #6: agent in infinite tool loop → recursion limit → fallback narrative."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": str(i), "type": "tool_call",
        }])
        for i in range(20)
    ]
    fake = fake_llm_factory(*canned)
    _patch_llm(monkeypatch, fake)

    from goal_planning.agent import run_goal_planning_agent
    response = await run_goal_planning_agent(
        user_message="Run forever",
        baseline_input=_baseline(),
        chat_session_id="test-6",
        anchor_date=anchor_date,
    )
    # Graceful fallback message (matches _RECURSION_LIMIT_MESSAGE in agent/prompts.py)
    assert "ran out" in response.narrative.lower() or "focused" in response.narrative.lower()
