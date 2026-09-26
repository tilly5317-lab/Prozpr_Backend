"""The AA preference what-if — RETIRED 2026-09-17 and UNREFERENCED in
production; these tests protect the re-enable seam, not live behaviour. The live
contract is `test_a_preference_ask_points_at_the_preferences_page` below.

When it ran, an AA preference ask reshaped the allocation and showed the
contrast, persisting NOTHING (spec 2026-09-16 D4) — no candidate row, no FK.

The context MUST be a real TurnContext: the handler passes it through
`with_chat_overrides`, which calls `dataclasses.replace` and rejects a
SimpleNamespace — and it is evaluated as an argument, so patching the engine
does not bypass it.
"""

from __future__ import annotations

import dataclasses
import uuid
from types import SimpleNamespace

import pytest

import app.domains.asset_allocation.services.aa_engine.chat as aa_chat
from app.domains.rebalancing.tests.detector_ctx import make_detector_ctx


def _ctx(question):
    ctx = make_detector_ctx(
        question,
        user_ctx=SimpleNamespace(
            id=uuid.uuid4(),
            first_name="A",
            personal_finance_profile=None,
            saved_investment_preference=None,
        ),
    )
    return dataclasses.replace(ctx, db=object())


def _breakdown(equity, debt, others):
    return SimpleNamespace(
        recommended=SimpleNamespace(
            equity_total_pct=equity, debt_total_pct=debt, others_total_pct=others
        )
    )


def _last_alloc():
    """The prior snapshot — the recommended allocation the what-if contrasts
    against, so no baseline recompute is needed."""
    return SimpleNamespace(
        output_payload={
            "allocation_result": {"client_summary": {"effective_risk_score": 5.5}}
        }
    )


@pytest.fixture
def stubs(monkeypatch):
    calls = {"compute": [], "reply": []}

    async def _fake_resolve(db, user, chat_intent, *, base_row=None):
        return (
            chat_intent,
            SimpleNamespace(
                asset_class_requested=None,
                subgroup_emphasis={"gold_commodities": 12.0},
                applied_defaults={},
            ),
            {"subgroups": {"gold_commodities": "more"}},
        )

    async def _fake_compute(user, question, **kw):
        calls["compute"].append(kw)
        return SimpleNamespace(
            blocking_message=None,
            result=SimpleNamespace(
                human_override_applied=SimpleNamespace(
                    preference_applied=True, shortfall_reason=None
                ),
                asset_class_breakdown=_breakdown(55.0, 30.0, 15.0),
            ),
        )

    async def _fake_reply(**kw):
        calls["reply"].append(kw)
        return "reply"

    def _fake_rehydrate(last_alloc):
        return SimpleNamespace(asset_class_breakdown=_breakdown(60.0, 30.0, 10.0))

    monkeypatch.setattr(aa_chat.prefs, "resolve_one_off", _fake_resolve)
    monkeypatch.setattr(aa_chat, "compute_allocation_result", _fake_compute)
    monkeypatch.setattr(aa_chat, "_reply_with_allocation_tables", _fake_reply)
    monkeypatch.setattr(aa_chat, "_rehydrate_last_alloc_output", _fake_rehydrate)
    return calls


async def test_preference_ask_runs_one_engine_call_and_persists_nothing(stubs):
    action = aa_chat.ChatAction(
        mode="counterfactual_explore",
        preference_asks=[{"target": "gold", "level": "more"}],
    )

    result = await aa_chat._handle_preference_what_if_aa(
        _ctx("add some gold"), action, _last_alloc()
    )

    assert result.text == "reply"
    # ONE engine call: the baseline is the snapshot already on screen.
    assert len(stubs["compute"]) == 1
    assert stubs["compute"][0]["db"] is None, "AA's what-if writes nothing"
    assert stubs["compute"][0]["persist_recommendation"] is False


async def test_the_contrast_carries_both_mixes_and_a_directive(stubs):
    action = aa_chat.ChatAction(
        mode="counterfactual_explore",
        preference_asks=[{"target": "gold", "level": "more"}],
    )

    await aa_chat._handle_preference_what_if_aa(
        _ctx("add some gold"), action, _last_alloc()
    )

    impact = stubs["reply"][0]["preference_impact"]
    assert impact["recommended_mix_pct"] == {"equity": 60.0, "debt": 30.0, "others": 10.0}
    assert impact["requested_mix_pct"] == {"equity": 55.0, "debt": 30.0, "others": 15.0}
    assert "tilt_note" in impact


async def test_an_unmappable_ask_says_so_and_emits_a_token(monkeypatch):
    relayed = {}
    unserved = {}

    async def _fake_relay(ctx, message):
        relayed["message"] = message
        return aa_chat.ChatHandlerResult(text=message)

    def _fake_unserved(**kw):
        unserved.update(kw)

    monkeypatch.setattr(aa_chat, "_relay_allocation", _fake_relay)
    monkeypatch.setattr(aa_chat, "capture_preference_unserved", _fake_unserved)

    action = aa_chat.ChatAction(
        mode="counterfactual_explore",
        preference_asks=[
            {"target": "other", "level": "more", "other_words": "ESG funds"}
        ],
    )

    result = await aa_chat._handle_preference_what_if_aa(
        _ctx("more ESG funds"), action, _last_alloc()
    )

    assert "ESG funds" in result.text
    assert unserved["flow"] == "asset_allocation"
    assert unserved["failure_class"] == "unmapped_category"


async def test_a_preference_ask_points_at_the_preferences_page(monkeypatch):
    """Ruling 2026-09-17: the extracted field still beats the mode label, but it
    now routes to the preferences pointer rather than the what-if below."""
    seen = {"what_if": 0}

    async def _never(ctx, action, last_alloc):
        seen["what_if"] += 1
        raise AssertionError("the what-if path is retired")

    async def _fake_relay(**kw):
        return kw["message"]

    monkeypatch.setattr(aa_chat, "_handle_preference_what_if_aa", _never)
    monkeypatch.setattr(aa_chat, "format_relay_or_canned", _fake_relay)

    action = aa_chat.ChatAction(
        mode="clarify",  # a mislabel the handler must override
        clarification_question="what risk score?",
        preference_asks=[{"target": "gold", "level": "more"}],
    )

    result = await aa_chat._dispatch_action(action, _last_alloc(), _ctx("add gold"))

    assert seen["what_if"] == 0
    assert result.show_preferences_pill is True
    assert "preferences page" in result.text
