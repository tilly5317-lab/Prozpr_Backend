"""The fund-house view is fetched only when the classifier asks for it.

Loading market context on every turn put valuation views into every "review my
portfolio", and the model reached for them. The gate lives on ``build_facts``
(the agent builds the facts pack the shared formatter answers from). The factual
``market_commentary`` channel has been dropped from portfolio entirely — the
Prozpr-only fund-house view is the sole market source, gated to judgement questions.
"""

from __future__ import annotations

import sys
from pathlib import Path


_SRC = Path(__file__).resolve().parents[4] / "AI_Agents" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from portfolio_query import orchestrator as orch  # noqa: E402
from portfolio_query.models import ClientContext, PortfolioContext  # noqa: E402


def _facts(monkeypatch, want_view: bool = False) -> dict:
    monkeypatch.setattr(orch, "_load_fund_house_view", lambda: "REAL VIEW TEXT")
    o = object.__new__(orch.PortfolioQueryOrchestrator)
    return o.build_facts(
        client=ClientContext(),
        portfolio=PortfolioContext(),
        want_fund_house_view=want_view,
    )


def test_view_is_skipped_when_not_requested(monkeypatch):
    got = _facts(monkeypatch, want_view=False)

    assert "REAL VIEW TEXT" not in got["fund_house_view"]
    assert "Not loaded" in got["fund_house_view"]


def test_view_is_loaded_when_requested(monkeypatch):
    got = _facts(monkeypatch, want_view=True)

    assert got["fund_house_view"] == "REAL VIEW TEXT"


def test_view_default_is_off_for_direct_callers():
    import inspect

    sig = inspect.signature(orch.PortfolioQueryOrchestrator.build_facts)

    assert sig.parameters["want_fund_house_view"].default is False


def test_no_market_commentary_channel():
    """The factual commentary channel is gone: no param, no fact key."""
    import inspect

    sig = inspect.signature(orch.PortfolioQueryOrchestrator.build_facts)
    assert "want_market_commentary" not in sig.parameters


def test_the_three_sources_are_present(monkeypatch):
    """Only the view is gated; profile and holdings are never skipped. No market_commentary."""
    got = _facts(monkeypatch, want_view=False)

    assert set(got) == {
        "fund_house_view",
        "client_profile",
        "current_portfolio",
    }


def test_service_sets_want_view_from_tools_needed(monkeypatch):
    """Regression guard: a judgement question tagged fund_house_view must reach the
    orchestrator as want_fund_house_view=True."""
    import asyncio
    from types import SimpleNamespace

    from app.domains.portfolio.services import portfolio_query_service as svc

    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text="ok",
            path=None,
            suggested_intent=None,
            show_preferences_pill=False,
        )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(svc, "generate_portfolio_query_response", fake_generate)
    monkeypatch.setattr(svc, "_record_path", _noop)
    monkeypatch.setattr(svc, "_record_intent_disagreement", _noop)

    ctx = SimpleNamespace(
        user_ctx=SimpleNamespace(first_name="A"),
        conversation_history=[],
        db=None,
        effective_user_id=1,
        tools_needed=("fund_house_view",),
    )
    asyncio.run(svc.answer_portfolio_query("is my portfolio too aggressive?", ctx))

    assert captured["want_fund_house_view"] is True
    assert "want_market_commentary" not in captured
