"""The detector's decision reaches the engine and the formatter.

goal_planning was the only formatter-driven module with no detector: it passed
``action_mode="narrate"`` hardcoded, so "will I meet my goals?" and "what if I
retire at 50?" were handled identically — the second one answered from a plan
computed at the stored retirement age, which is data the question was not about.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.cashflow.services.goal_planning_engine.chat as chat


@pytest.fixture
def harness(monkeypatch):
    seen: dict = {"overrides": "unset", "action_mode": None, "formatted": False}

    async def _snapshot(**kwargs):
        seen["overrides"] = kwargs.get("overrides")
        return SimpleNamespace(
            facts_pack={}, fallback_text="fb", snapshot=None, plan_run_id=None
        )

    async def _format(**kwargs):
        seen["formatted"] = True
        seen["action_mode"] = kwargs.get("action_mode")
        return "formatted reply"

    monkeypatch.setattr(chat, "compute_goal_planning_snapshot", _snapshot)
    monkeypatch.setattr(chat, "format_with_telemetry", _format)
    monkeypatch.setattr(chat, "_build_cashflow_chart_payloads", lambda s: [])
    return seen


def _ctx():
    return SimpleNamespace(
        user_ctx=SimpleNamespace(first_name="X"),
        user_question="q",
        session_id=uuid.uuid4(),
        db=None,
        conversation_history=[],
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        chat_overrides=None,
    )


def _detects(monkeypatch, action):
    async def _fake(ctx):
        return action

    monkeypatch.setattr(chat, "_detect_goal_action", _fake)


@pytest.mark.asyncio
async def test_plain_question_narrates_with_no_overrides(harness, monkeypatch):
    _detects(monkeypatch, chat.GoalChatAction(mode="narrate"))

    await chat.goal_planning_chat(_ctx())

    assert harness["overrides"] is None
    assert harness["action_mode"] == "narrate"


@pytest.mark.asyncio
async def test_counterfactual_reaches_the_engine_and_the_formatter(harness, monkeypatch):
    _detects(
        monkeypatch,
        chat.GoalChatAction(
            mode="counterfactual_explore", overrides={"retirement_age": 50}
        ),
    )

    await chat.goal_planning_chat(_ctx())

    assert harness["overrides"] == {"retirement_age": 50}
    assert harness["action_mode"] == "counterfactual_explore"


@pytest.mark.asyncio
async def test_clarify_asks_the_question_and_skips_the_engine(harness, monkeypatch):
    _detects(
        monkeypatch,
        chat.GoalChatAction(
            mode="clarify", clarification_question="Retire at what age?"
        ),
    )

    result = await chat.goal_planning_chat(_ctx())

    assert result.text == "Retire at what age?"
    assert harness["overrides"] == "unset", "the engine must not run"
    assert harness["formatted"] is False, "clarify bypasses the formatter"


@pytest.mark.asyncio
async def test_clarify_without_a_question_falls_back_instead_of_computing(
    harness, monkeypatch
):
    """An empty clarification_question used to fall through: the engine ran and
    the formatter was handed action_mode='clarify', which has no writing rules."""
    _detects(monkeypatch, chat.GoalChatAction(mode="clarify"))

    result = await chat.goal_planning_chat(_ctx())

    assert result.text == chat._DEFAULT_CLARIFY_FALLBACK
    assert harness["overrides"] == "unset", "the engine must not run"
    assert harness["formatted"] is False
