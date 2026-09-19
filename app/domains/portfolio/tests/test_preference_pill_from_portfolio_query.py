"""portfolio_query is where preference READOUTS land (measured 0.85 cold /
0.92 mid-conversation), so it is the pill's most important producer — and the
one with no coverage until now. The tool field was also renamed
(`wants_preference_change` → `preference_question`) with nothing pinning it, so
a typo there would silently kill the pill on that whole route.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import app.domains.portfolio.services.portfolio_query_service as svc


def _ctx():
    return SimpleNamespace(
        user_ctx=SimpleNamespace(id=uuid.uuid4(), first_name="A"),
        conversation_history=[],
        db=None,
        effective_user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        tools_needed=(),
    )


def test_the_tool_field_the_agent_sets_is_declared():
    """The agent guide instructs `preference_question`; if the declared field
    and the guide disagree, the flag never arrives."""
    assert "preference_question" in svc._PORTFOLIO_TOOL_FIELDS
    assert svc._PORTFOLIO_TOOL_FIELDS["preference_question"]["type"] == "boolean"


async def test_a_preference_question_raises_the_pill(monkeypatch):
    async def _fake(**kw):
        return svc.PortfolioQueryOutcome(
            text="preferences live on your preferences page",
            show_preferences_pill=True,
        )

    monkeypatch.setattr(svc, "generate_portfolio_query_response", _fake)

    reply = await svc.answer_portfolio_query("reset my preferences", _ctx())

    assert reply.show_preferences_pill is True


async def test_an_ordinary_holdings_question_does_not(monkeypatch):
    async def _fake(**kw):
        return svc.PortfolioQueryOutcome(text="you hold 12 funds")

    monkeypatch.setattr(svc, "generate_portfolio_query_response", _fake)

    reply = await svc.answer_portfolio_query("what do I hold?", _ctx())

    assert reply.show_preferences_pill is False


def test_the_agent_guide_and_the_tool_field_agree():
    """The guide is rendered verbatim into the system prompt, so a stale field
    name there is a silent failure."""
    from pathlib import Path

    guide = Path(svc.__file__).parents[4] / "AI_Agents/src/portfolio_query/portfolio_query.md"
    text = guide.read_text(encoding="utf-8")
    assert "preference_question" in text
    assert "wants_preference_change" not in text, "stale field name in the guide"
