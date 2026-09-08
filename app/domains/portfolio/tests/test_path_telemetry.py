"""portfolio_query reports which internal path it took.

This agent has no action detector and needs none — it makes one LLM call and has
no deterministic work to gate a decision on. But it DOES classify every question
internally into Path X (out of scope), Path M (market) or Path P (portfolio), and
that choice was invisible: it shaped the reply and nothing recorded it.

Reporting it costs nothing — one more field on a tool call the agent already makes.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.portfolio.services.portfolio_query_service as svc


@pytest.fixture
def recorded(monkeypatch):
    calls: list[dict] = []

    async def _fake(db, **kwargs):
        calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(svc, "record_ai_module_run", _fake)
    return calls


def _ctx():
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


def test_the_outcome_carries_the_path():
    assert svc.PortfolioQueryOutcome(text="answer", path="P").path == "P"


@pytest.mark.asyncio
async def test_the_path_is_recorded(recorded, monkeypatch):
    _returns(svc.PortfolioQueryOutcome(text="answer", path="M"), monkeypatch)

    await svc.answer_portfolio_query("is the market bullish?", _ctx())

    assert len(recorded) == 1
    row = recorded[0]
    assert row["reason"] == "path"
    assert row["extra"]["path"] == "M"


@pytest.mark.asyncio
async def test_no_path_means_no_row(recorded, monkeypatch):
    """Older replies and the local failure paths carry no path — record nothing."""
    _returns(svc.PortfolioQueryOutcome(text="answer"), monkeypatch)

    await svc.answer_portfolio_query("q", _ctx())

    assert recorded == []


@pytest.mark.asyncio
async def test_path_and_disagreement_are_separate_rows(recorded, monkeypatch):
    """Path is per-turn; a disagreement is rare. Keep them queryable apart."""
    _returns(
        svc.PortfolioQueryOutcome(
            text="answer", path="P", suggested_intent="goal_planning"
        ),
        monkeypatch,
    )

    await svc.answer_portfolio_query("will I meet my goals?", _ctx())

    assert sorted(r["reason"] for r in recorded) == ["intent_disagreement", "path"]
