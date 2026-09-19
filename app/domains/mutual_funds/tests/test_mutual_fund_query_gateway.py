"""Tests for answer_mutual_fund_query — the chat gateway (orchestrator + resolver stubbed)."""

from __future__ import annotations

import types

import pytest

from app.domains.mutual_funds.services import mutual_fund_query_service as S
from app.domains.mutual_funds.services.fund_resolver_service import Ambiguous, ResolvedFund
from app.domains.mutual_funds.services.fund_screener_service import ScreenedFund
from mutual_fund_query import ExtractResult, MutualFundQueryFacts


def _async_ret(value):
    async def f(*a, **k):
        return value
    return f


class StubOrch:
    """The orchestrator only extracts; the shared formatter writes both replies."""

    narrate_body = "NARRATE BODY"

    def __init__(self, extracted):
        self._e = extracted

    async def extract(self, q, h):
        return self._e


def _capture_formatter(monkeypatch, reply="FORMATTED"):
    """Stub format_with_telemetry, recording the kwargs it was called with."""
    calls: list[dict] = []

    async def fake_format(**kwargs):
        calls.append(kwargs)
        return reply

    monkeypatch.setattr(S, "format_with_telemetry", fake_format)
    return calls


def _ctx():
    return types.SimpleNamespace(
        db=object(),  # sentinel — resolver/builder are stubbed, never touch it
        user_ctx=types.SimpleNamespace(portfolios=[]),
        conversation_history=[],
    )


@pytest.mark.asyncio
async def test_single_fund_goes_through_the_shared_formatter(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=["Parag Parikh"], asked_for="reasoning"))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)
    monkeypatch.setattr(S, "resolve_fund",
                        _async_ret(ResolvedFund("119771", "Parag Parikh - Direct - Growth", "INF001")))
    monkeypatch.setattr(
        S, "build_mutual_fund_query_facts",
        _async_ret(MutualFundQueryFacts(funds=[S.FundFacts(fund_name="Parag Parikh Flexi Cap")])),
    )
    calls = _capture_formatter(monkeypatch, "Because it has a long track record.")

    out = await S.answer_mutual_fund_query("why Parag Parikh?", _ctx())

    assert out == "Because it has a long track record."
    assert len(calls) == 1
    assert calls[0]["module_name"] == "mutual_fund_query"
    assert calls[0]["action_mode"] == "fund_detail"
    assert calls[0]["body_prompt"] == "NARRATE BODY"     # still sourced from the skill .md
    assert "Parag Parikh Flexi Cap" in str(calls[0]["facts_pack"])


@pytest.mark.asyncio
async def test_formatter_failure_falls_back_to_the_deterministic_brief(monkeypatch):
    """format_with_telemetry calls build_fallback itself; check ours is usable."""
    facts = MutualFundQueryFacts(
        funds=[
            S.FundFacts(
                fund_name="Alpha Flexi Cap",
                returns=S.FundReturns(return_1y_cagr_pct=12.34, return_5y_cagr_pct=18.5),
                house_reason="Consistent across cycles.",
            )
        ]
    )
    brief = S._fund_detail_fallback(facts)

    assert "Alpha Flexi Cap" in brief
    assert "1y 12.3%" in brief and "5y 18.5%" in brief
    assert "3y" not in brief                      # absent horizon is omitted, not estimated
    assert "Consistent across cycles." in brief


@pytest.mark.asyncio
async def test_fallback_is_honest_when_no_returns_are_stored():
    facts = MutualFundQueryFacts(funds=[S.FundFacts(fund_name="New Fund")])

    brief = S._fund_detail_fallback(facts)

    assert "no track record stored yet" in brief


@pytest.mark.asyncio
async def test_empty_fund_names_clarifies_without_formatting(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=[], asked_for="reasoning"))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)
    calls = _capture_formatter(monkeypatch)

    out = await S.answer_mutual_fund_query("tell me about your funds", _ctx())

    assert "which fund" in out.lower()
    assert calls == []


@pytest.mark.asyncio
async def test_ambiguous_fund_asks_clarifying_question(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=["HDFC"], asked_for="returns"))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)
    monkeypatch.setattr(S, "resolve_fund",
                        _async_ret(Ambiguous(["HDFC Flexi Cap", "HDFC Balanced Advantage"])))
    calls = _capture_formatter(monkeypatch)

    out = await S.answer_mutual_fund_query("HDFC returns?", _ctx())

    assert "did you mean" in out.lower()
    assert calls == []


@pytest.mark.asyncio
async def test_screen_ask_routes_to_screener_and_formatter(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=[], asked_for="returns", is_screen=True))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)

    captured = {}

    async def fake_screen(db, *, horizon_years, category, limit):
        captured.update(horizon=horizon_years, category=category, limit=limit)
        return [ScreenedFund("1", "Alpha Fund", "Alpha AMC", "Large Cap Fund", 3, 25.99)]

    monkeypatch.setattr(S, "screen_top_funds", fake_screen)
    calls = _capture_formatter(monkeypatch, "Top funds right now: 1. Alpha Fund (26% over 3y).")

    out = await S.answer_mutual_fund_query("which are the best performing mutual funds?", _ctx())

    assert out == "Top funds right now: 1. Alpha Fund (26% over 3y)."
    assert calls[0]["action_mode"] == "screen"   # the other branch keeps its own mode
    assert captured["horizon"] == 3      # default when none named
    assert captured["limit"] == 5
    assert captured["category"] is None
    assert "Alpha Fund" in str(calls[0]["facts_pack"])


@pytest.mark.asyncio
async def test_screen_honors_named_horizon_and_category(monkeypatch):
    orch = StubOrch(
        ExtractResult(
            fund_names=[], asked_for="returns", is_screen=True,
            screen_category="large cap", screen_horizon_years=5,
        )
    )
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)

    captured = {}

    async def fake_screen(db, *, horizon_years, category, limit):
        captured.update(horizon=horizon_years, category=category)
        return [ScreenedFund("1", "A", "AMC", "Large Cap Fund", 5, 20.0)]

    monkeypatch.setattr(S, "screen_top_funds", fake_screen)
    _capture_formatter(monkeypatch, "ok")

    await S.answer_mutual_fund_query("best large cap funds over 5 years", _ctx())

    assert captured["horizon"] == 5
    assert captured["category"] == "Large Cap Fund"   # free text canonicalised


@pytest.mark.asyncio
async def test_screen_empty_result_is_honest(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=[], asked_for="returns", is_screen=True))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)
    monkeypatch.setattr(S, "screen_top_funds", _async_ret([]))

    out = await S.answer_mutual_fund_query("best funds?", _ctx())

    assert "enough" in out.lower() or "couldn't" in out.lower()


@pytest.mark.asyncio
async def test_two_named_funds_passed_to_builder(monkeypatch):
    orch = StubOrch(ExtractResult(fund_names=["A", "B"], asked_for="comparison"))
    monkeypatch.setattr(S, "_get_orchestrator", lambda: orch)
    monkeypatch.setattr(S, "resolve_fund", _async_ret(ResolvedFund("1", "A", "INF001")))

    captured = {}

    async def fake_build(db, resolved, asked_for):
        captured["n"] = len(resolved)
        return MutualFundQueryFacts(funds=[])

    monkeypatch.setattr(S, "build_mutual_fund_query_facts", fake_build)
    _capture_formatter(monkeypatch, "Here's the comparison.")

    out = await S.answer_mutual_fund_query("compare A and B", _ctx())

    assert out == "Here's the comparison."
    assert captured["n"] == 2
