"""Record when the portfolio agent thinks the router sent it the wrong question.

The agent reports its opinion; it never acts on it. Reaching this module at all
means the router chose ``portfolio_query``, so a populated ``suggested_intent``
IS the disagreement. Accumulating these tells us whether the router genuinely
misroutes often enough to justify building a handoff — on the one incident we
have (2026-07-25) the router was right and the agent was the confused one, so
the error rate is worth measuring before any routing machinery gets built.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.portfolio.services.portfolio_query_service as svc


@pytest.fixture
def recorded(monkeypatch):
    """Capture record_ai_module_run kwargs instead of writing to a DB."""
    calls: list[dict] = []

    async def _fake(db, **kwargs):
        calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(svc, "record_ai_module_run", _fake)
    return calls


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        user_ctx=SimpleNamespace(id=uuid.uuid4()),
        conversation_history=[],
        session_id=uuid.uuid4(),
        db=object(),
        effective_user_id=uuid.uuid4(),
    )


def _returns(outcome, monkeypatch):
    async def _fake(user, user_question, conversation_history=None, **kwargs):
        return outcome

    monkeypatch.setattr(svc, "generate_portfolio_query_response", _fake)


@pytest.mark.asyncio
async def test_disagreement_is_recorded(recorded, monkeypatch):
    _returns(
        svc.PortfolioQueryOutcome(text="answer", suggested_intent="goal_planning"),
        monkeypatch,
    )

    text = await svc.answer_portfolio_query("Review my portfolio", _ctx())

    assert text == "answer", "the customer still gets the answer — reporting only"
    assert len(recorded) == 1
    row = recorded[0]
    assert row["module"] == "portfolio_query"
    assert row["reason"] == "intent_disagreement"
    assert row["intent_detected"] == "portfolio_query"
    assert row["extra"]["agent_suggested_intent"] == "goal_planning"
    assert row["extra"]["question"] == "Review my portfolio"


@pytest.mark.asyncio
async def test_agreement_records_nothing(recorded, monkeypatch):
    """No opinion means no disagreement — the table stays a list of real ones."""
    _returns(svc.PortfolioQueryOutcome(text="answer"), monkeypatch)

    await svc.answer_portfolio_query("Review my portfolio", _ctx())

    assert recorded == []


@pytest.mark.asyncio
async def test_agent_naming_its_own_intent_is_not_a_disagreement(recorded, monkeypatch):
    _returns(
        svc.PortfolioQueryOutcome(text="answer", suggested_intent="portfolio_query"),
        monkeypatch,
    )

    await svc.answer_portfolio_query("Review my portfolio", _ctx())

    assert recorded == []
