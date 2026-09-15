"""AINV chat preference path: the extractor's `preference_asks`, the service's
preference-id plumbing, and the what-if handler over spied engine / formatter /
DB seams. No live API."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.additional_investment.services.ainv_engine import chat as chat_mod
from app.domains.additional_investment.services.ainv_engine import (
    service as service_mod,
)
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.profile.services.preference_lexicon import PreferenceAsk


def test_deploy_request_carries_preference_asks():
    request = chat_mod._DeployRequest(
        amount_inr=5000,
        cadence="sip_monthly",
        preference_asks=[{"target": "small_cap", "level": "heavy"}],
    )
    assert request.preference_asks[0].target == "small_cap"
    assert request.preference_asks[0].level == "heavy"


def test_extract_returns_preference_asks():
    fake_result = chat_mod._DeployRequest(
        amount_inr=25000,
        cadence="sip_monthly",
        preference_asks=[{"target": "small_cap", "level": "heavy"}],
    )
    with patch.object(chat_mod, "classify_action", new=AsyncMock(return_value=fake_result)):
        amount, cadence, raw_category, preference_asks = asyncio.run(
            chat_mod.extract_deploy_request("start a 25k SIP, mostly small cap")
        )

    assert amount == 25000
    assert cadence.value == "sip_monthly"
    assert raw_category is None
    assert preference_asks is not None
    assert preference_asks[0].target == "small_cap"
    assert preference_asks[0].level == "heavy"


def test_extract_regex_fallback_has_no_asks():
    with patch.object(
        chat_mod, "classify_action", new=AsyncMock(side_effect=RuntimeError("api down"))
    ):
        amount, cadence, raw_category, preference_asks = asyncio.run(
            chat_mod.extract_deploy_request("invest 50k as lumpsum")
        )

    assert amount == 50000.0
    assert preference_asks is None


_DERIVED_SENTINEL = uuid.uuid4()


def _run_compute(monkeypatch, *, saved_investment_preference_id=service_mod.DERIVE_PREFERENCE_ID, persist=True, builder_error=None):
    """Patch every seam ``compute_additional_investment_result`` touches and
    run it once; returns ``(outcome, paa_persist_kwargs, ainv_persist_kwargs, paa_result)``."""
    paa_result = SimpleNamespace(human_override_applied=None, aggregated_subgroups=[])
    paa_outcome = SimpleNamespace(result=paa_result, blocking_message=None)
    inp_fake = SimpleNamespace()
    response_fake = SimpleNamespace(
        buys=[SimpleNamespace(asset_subgroup="equity", amount_inr=100.0)],
        per_subgroup_target=[],
    )
    paa_persist_calls: list[dict] = []
    ainv_persist_calls: list[dict] = []

    async def _fake_persist_paa(db, **kwargs):
        paa_persist_calls.append(kwargs)
        return uuid.uuid4()

    async def _fake_persist_ainv(db, acting_user_id, response, **kwargs):
        ainv_persist_calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(
        service_mod, "compute_practical_allocation_result", AsyncMock(return_value=paa_outcome)
    )
    monkeypatch.setattr(
        service_mod,
        "build_additional_investment_input_for_user",
        AsyncMock(return_value=(inp_fake, {})),
    )
    monkeypatch.setattr(service_mod, "run_additional_investment", lambda inp: response_fake)
    monkeypatch.setattr(
        service_mod, "latest_buy_trades_by_subgroup", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service_mod, "persist_practical_allocation_run", _fake_persist_paa)
    monkeypatch.setattr(
        service_mod, "persist_additional_investment_recommendation", _fake_persist_ainv
    )
    monkeypatch.setattr(service_mod, "set_starting_monthly_investment", AsyncMock())
    monkeypatch.setattr(service_mod, "mark_cashflow_stale", AsyncMock())
    monkeypatch.setattr(
        service_mod, "preference_id_for", lambda user, *, applied: _DERIVED_SENTINEL
    )
    if builder_error is not None:
        monkeypatch.setattr(
            service_mod,
            "build_additional_investment_input_for_user",
            AsyncMock(side_effect=builder_error),
        )

    outcome = asyncio.run(
        service_mod.compute_additional_investment_result(
            SimpleNamespace(),
            "deploy 5000 monthly",
            db=None,
            acting_user_id=uuid.uuid4(),
            chat_session_id=None,
            deploy_amount_inr=5000.0,
            cadence=service_mod.Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(),
            persist=persist,
            saved_investment_preference_id=saved_investment_preference_id,
        )
    )
    return outcome, paa_persist_calls, ainv_persist_calls, paa_result


def test_compute_uses_explicit_preference_id_on_both_persists(monkeypatch):
    explicit_id = uuid.uuid4()
    _outcome, paa_calls, ainv_calls, _ = _run_compute(
        monkeypatch, saved_investment_preference_id=explicit_id
    )
    assert paa_calls[0]["saved_investment_preference_id"] == explicit_id
    assert ainv_calls[0]["saved_investment_preference_id"] == explicit_id

    _outcome2, paa_calls2, ainv_calls2, _ = _run_compute(monkeypatch)
    assert paa_calls2[0]["saved_investment_preference_id"] == _DERIVED_SENTINEL
    assert ainv_calls2[0]["saved_investment_preference_id"] == _DERIVED_SENTINEL


def test_outcome_carries_practical_result(monkeypatch):
    outcome, _paa_calls, _ainv_calls, paa_result = _run_compute(monkeypatch, persist=False)
    assert outcome.practical_result is paa_result


CANDIDATE_ID = uuid.UUID("c0ffee00-aaaa-4bbb-8ccc-ddddeeee0002")


def _buy(fund, monthly=None, amount=0.0):
    return SimpleNamespace(
        recommended_fund=fund,
        sub_category="Large Cap Fund",
        amount_inr=amount,
        monthly_amount_inr=monthly,
    )


def _ainv_output(buys):
    return SimpleNamespace(
        deploy_amount_inr=25000.0,
        undeployed_inr=0.0,
        cadence=SimpleNamespace(value="sip_monthly"),
        target_bucket=SimpleNamespace(value="long_term"),
        buys=buys,
        per_subgroup_target=[
            SimpleNamespace(subgroup="low_beta_equities", ratio=1.0, target_inr=25000.0)
        ],
    )


_BASELINE_BUYS = [_buy("HDFC Top 100", monthly=20000.0), _buy("Axis Small Cap", monthly=5000.0)]
_REQUESTED_BUYS = [_buy("HDFC Top 100", monthly=8000.0), _buy("Axis Small Cap", monthly=17000.0)]


def _ainv_ctx(question="start a 25k SIP, mostly small cap"):
    return TurnContext(
        user_ctx=SimpleNamespace(id=uuid.uuid4(), first_name="A", risk_profile=None),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=SimpleNamespace(flush=AsyncMock()),
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="additional_investment",
    )


def _ask(target, level="more", number=None, other_words=None):
    return PreferenceAsk(
        target=target, level=level, number=number, other_words=other_words
    )


@pytest.fixture
def spy(monkeypatch):
    calls = {"compute": [], "format": [], "relay": [], "telemetry": [], "resolve": [],
             "resolve_base": [], "insert": [], "fill": [], "order": []}
    resolved = SimpleNamespace(
        asset_class_requested={"equity": 70.0, "debt": 25.0, "others": 5.0},
        subgroup_emphasis={"high_beta_equities": "heavy"},
    )
    state = {
        "changed": {"subgroups": {"high_beta_equities": "heavy"}},
        "applied": SimpleNamespace(preference_applied=True, shortfall_reason=None),
        # The ACHIEVED mix is the PAA run's own class breakdown (Task C2), not
        # a field carried on human_override_applied.
        "achieved": {"equity": 69.0, "debt": 26.0, "others": 5.0},
        "blocking": [None, None],
        "run_ids": [None, "candidate-run-id"],
    }

    async def fake_compute(user, question, **kw):
        calls["compute"].append(kw)
        calls["order"].append("compute")
        n = len(calls["compute"]) - 1
        blocking = state["blocking"][n] if n < len(state["blocking"]) else None
        run_id = state["run_ids"][n] if n < len(state["run_ids"]) else None
        return SimpleNamespace(
            output=_ainv_output(_REQUESTED_BUYS if kw.get("persist") else _BASELINE_BUYS),
            run_id=run_id,
            blocking_message=blocking,
            deficit_facts=None,
            practical_result=SimpleNamespace(
                human_override_applied=state["applied"],
                asset_class_breakdown=SimpleNamespace(
                    recommended=SimpleNamespace(
                        equity_total_pct=state["achieved"]["equity"],
                        debt_total_pct=state["achieved"]["debt"],
                        others_total_pct=state["achieved"]["others"],
                    )
                ),
            ),
        )

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    async def fake_relay(**kw):
        calls["relay"].append(kw)
        return kw.get("message", "")

    async def fake_resolve(db, user, chat_intent, *, base_row=None):
        calls["resolve"].append(chat_intent)
        calls["resolve_base"].append(base_row)
        return chat_intent, resolved, state["changed"]

    async def fake_insert(db, user, intent, res, achieved):
        calls["insert"].append({"intent": intent, "achieved": achieved})
        calls["order"].append("insert")
        return SimpleNamespace(id=CANDIDATE_ID)

    monkeypatch.setattr(chat_mod, "compute_additional_investment_result", fake_compute)
    monkeypatch.setattr(chat_mod, "format_with_telemetry", fake_format)
    monkeypatch.setattr(chat_mod, "format_relay_or_canned", fake_relay)
    monkeypatch.setattr(chat_mod, "capture_preference_unserved",
                        lambda **kw: calls["telemetry"].append(kw))
    monkeypatch.setattr(chat_mod, "_session_candidate_id", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod.prefs, "resolve_one_off", fake_resolve)
    monkeypatch.setattr(chat_mod.prefs, "insert_candidate", fake_insert)
    monkeypatch.setattr(chat_mod.prefs, "fill_candidate_targets",
                        lambda candidate, achieved, shortfall_reason=None: calls["fill"].append(achieved))
    calls["state"] = state
    return calls


async def test_what_if_runs_baseline_then_the_requested_one_off(spy):
    ctx = _ainv_ctx()
    result = await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert len(spy["compute"]) == 2
    assert spy["compute"][0]["persist"] is False
    assert spy["compute"][1]["persist"] is True
    assert spy["compute"][1]["saved_investment_preference_id"] == CANDIDATE_ID
    ov = spy["compute"][1]["chat_ctx"].chat_overrides
    assert ov["human_override_preferences"] == {
        "asset_class_requested": {"equity": 70.0, "debt": 25.0, "others": 5.0},
        "subgroup_emphasis": {"high_beta_equities": "heavy"},
    }
    assert spy["compute"][0]["chat_ctx"].chat_overrides is None
    assert result.additional_investment_run_id == "candidate-run-id"


async def test_what_if_surfaces_the_run_cadence_for_view_plan_routing(spy):
    # The chat "View plan" button routes to the SIP vs Lump sum tab by cadence,
    # so the run's cadence must ride out alongside its id.
    ctx = _ainv_ctx()
    result = await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.LUMPSUM, [_ask("small_cap", "heavy")], None, None
    )
    assert result.additional_investment_run_id == "candidate-run-id"
    assert result.additional_investment_cadence == "lumpsum"


async def test_candidate_row_is_inserted_before_the_requested_run_and_filled_after(spy):
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert spy["order"] == ["compute", "insert", "compute"]
    assert spy["insert"][0]["achieved"] is None
    assert spy["fill"] == [{"equity": 69.0, "debt": 26.0, "others": 5.0}]
    ctx.db.flush.assert_awaited()


async def test_facts_carry_the_preference_block(spy):
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    facts = spy["format"][0]["facts_pack"]
    pref = facts["preference"]
    assert pref["customer_choices"] == {"subgroups": {"high_beta_equities": "heavy"}}
    assert pref["save_hint"] is True
    assert "not_applied" not in pref and "shortfall" not in pref
    changes = {c["fund"]: c["change_indian"] for c in pref["buy_changes_vs_recommended"]}
    assert changes["Axis Small Cap"].startswith("+")
    assert changes["HDFC Top 100"].startswith("−")


async def test_save_hint_is_withheld_when_the_requested_run_did_not_persist(spy):
    spy["state"]["run_ids"] = [None, None]
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert not spy["format"][0]["facts_pack"]["preference"].get("save_hint")


async def test_shortfall_rides_on_the_preference_block(spy):
    spy["state"]["applied"] = SimpleNamespace(
        preference_applied=True, shortfall_reason="emergency buffer kept intact",
    )
    spy["state"]["achieved"] = {"equity": 60.0, "debt": 35.0, "others": 5.0}
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert spy["format"][0]["facts_pack"]["preference"]["shortfall"] == (
        "emergency buffer kept intact"
    )


async def test_unmapped_only_ask_still_deploys_and_discloses_the_words(spy):
    """Spec 3.2: AINV's whole output IS the buy list, so an ask we cannot shape
    by never costs the customer their deployment — it is disclosed, not declined."""
    ctx = _ainv_ctx("invest 25k a month, only banking funds")
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY,
        [_ask("other", "more", other_words="banking funds")], None, None,
    )
    assert len(spy["compute"]) == 1 and spy["compute"][0]["persist"] is True
    assert spy["insert"] == [] and spy["relay"] == []
    assert spy["telemetry"] == [dict(flow="additional_investment",
                                     failure_class="unmapped_category",
                                     session_id=ctx.session_id,
                                     distinct_id=ctx.effective_user_id)]
    assert spy["format"][0]["facts_pack"]["preference"] == {
        "not_applied": ["banking funds"]
    }


async def test_partially_unmapped_ask_runs_and_discloses_the_rest(spy):
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY,
        [_ask("small_cap", "heavy"), _ask("other", "more", other_words="banking funds")],
        None, None,
    )
    assert len(spy["compute"]) == 2, "the mapped part still runs"
    assert spy["telemetry"][0]["failure_class"] == "unmapped_category"
    assert spy["format"][0]["facts_pack"]["preference"]["not_applied"] == ["banking funds"]


async def test_ask_identical_to_the_saved_preference_just_deploys(spy):
    spy["state"]["changed"] = {}
    ctx = _ainv_ctx()
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert len(spy["compute"]) == 1 and spy["compute"][0]["persist"] is True
    assert spy["insert"] == [] and spy["relay"] == []
    assert spy["format"][0]["facts_pack"]["preference"] == {"already_saved": True}


async def test_blocking_baseline_relays_and_never_inserts_a_candidate(spy):
    spy["state"]["blocking"] = ["complete your profile first", None]
    ctx = _ainv_ctx()
    result = await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert len(spy["compute"]) == 1 and spy["insert"] == []
    assert spy["relay"][0]["message"] == "complete your profile first"
    assert result.additional_investment_run_id is None


async def test_live_session_candidate_is_the_merge_base(spy, monkeypatch):
    live = SimpleNamespace(id=CANDIDATE_ID, is_active=False, activated_at=None)
    monkeypatch.setattr(chat_mod, "_session_candidate_id",
                        AsyncMock(return_value=CANDIDATE_ID))
    monkeypatch.setattr(chat_mod.prefs, "candidate_row", AsyncMock(return_value=live))
    ctx = _ainv_ctx("add some gold to that")
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("gold")], None, None
    )
    assert spy["resolve_base"] == [live]


async def test_an_already_saved_session_candidate_is_ignored(spy, monkeypatch):
    from datetime import datetime, timezone

    saved = SimpleNamespace(id=CANDIDATE_ID, is_active=True,
                            activated_at=datetime.now(timezone.utc))
    monkeypatch.setattr(chat_mod, "_session_candidate_id",
                        AsyncMock(return_value=CANDIDATE_ID))
    monkeypatch.setattr(chat_mod.prefs, "candidate_row", AsyncMock(return_value=saved))
    ctx = _ainv_ctx("add some gold to that")
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("gold")], None, None
    )
    assert spy["resolve_base"] == [None]


async def test_handle_routes_a_preference_ask_to_the_what_if(spy, monkeypatch):
    seen = {}

    async def fake_what_if(ctx, amount, cadence, asks, raw_category, category):
        seen.update(amount=amount, cadence=cadence, asks=asks,
                    raw_category=raw_category, category=category)
        return "WHATIF"

    monkeypatch.setattr(chat_mod, "_handle_preference_what_if_ainv", fake_what_if)
    monkeypatch.setattr(chat_mod, "resolve_category", lambda raw: "RESOLVED")
    monkeypatch.setattr(
        chat_mod, "extract_deploy_request",
        AsyncMock(return_value=(25000.0, chat_mod.Cadence.SIP_MONTHLY, "smallcap",
                                [_ask("small_cap", "heavy")])),
    )
    assert await chat_mod.handle(_ainv_ctx()) == "WHATIF"
    assert seen["amount"] == 25000.0 and seen["asks"][0].target == "small_cap"
    assert (seen["raw_category"], seen["category"]) == ("smallcap", "RESOLVED")


async def test_a_preference_ask_without_an_amount_still_asks_for_the_amount(spy, monkeypatch):
    called = {"what_if": False}

    async def fake_what_if(*a, **kw):
        called["what_if"] = True
        return "WHATIF"

    monkeypatch.setattr(chat_mod, "_handle_preference_what_if_ainv", fake_what_if)
    monkeypatch.setattr(
        chat_mod, "extract_deploy_request",
        AsyncMock(return_value=(None, chat_mod.Cadence.LUMPSUM, None,
                                [_ask("small_cap", "heavy")])),
    )
    result = await chat_mod.handle(_ainv_ctx("go small-cap heavy"))
    assert called["what_if"] is False
    assert result.text == chat_mod._MSG_ASK_AMOUNT


async def test_an_ordinary_turn_carries_no_preference_facts(spy, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "extract_deploy_request",
        AsyncMock(return_value=(25000.0, chat_mod.Cadence.SIP_MONTHLY, None, None)),
    )
    await chat_mod.handle(_ainv_ctx("start a 25k SIP"))
    assert len(spy["compute"]) == 1 and spy["insert"] == []
    assert "preference" not in spy["format"][0]["facts_pack"]


async def test_an_ordinary_deploy_surfaces_no_run_id(spy, monkeypatch):
    """The persisted run exists, but its id is NOT surfaced on an ordinary
    deploy — the chat "Save preference" pill (the field's only client) must
    appear ONLY on preference what-if turns."""
    spy["state"]["run_ids"] = ["ordinary-run-id"]  # ordinary compute still persists a run
    monkeypatch.setattr(
        chat_mod, "extract_deploy_request",
        AsyncMock(return_value=(25000.0, chat_mod.Cadence.SIP_MONTHLY, None, None)),
    )
    result = await chat_mod.handle(_ainv_ctx("start a 25k SIP"))
    assert len(spy["compute"]) == 1  # ordinary path: a single compute, no baseline+requested pair
    assert result.additional_investment_run_id is None


async def test_a_db_less_turn_degrades_to_the_ordinary_deploy(spy):
    """No session → nowhere to write the candidate row the pill activates, so
    the what-if degrades to the plain deploy (mirrors rebalancing)."""
    ctx = dataclasses.replace(_ainv_ctx(), db=None)
    await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert len(spy["compute"]) == 1 and spy["compute"][0]["persist"] is True
    assert spy["insert"] == [] and spy["resolve"] == []
    assert "preference" not in spy["format"][0]["facts_pack"]


async def test_a_failed_candidate_flush_still_answers(spy):
    ctx = dataclasses.replace(
        _ainv_ctx(),
        db=SimpleNamespace(flush=AsyncMock(side_effect=RuntimeError("pending rollback"))),
    )
    result = await chat_mod._handle_preference_what_if_ainv(
        ctx, 25000.0, chat_mod.Cadence.SIP_MONTHLY, [_ask("small_cap", "heavy")], None, None
    )
    assert result.text == "formatted"
    assert spy["format"][0]["facts_pack"]["preference"]["save_hint"] is True


def test_a_blocking_outcome_carries_no_practical_result(monkeypatch):
    """A gate has no plan to read a preference off; carrying the PAA result there
    invites the what-if handler to treat a blocked run as an applied one."""
    outcome, _paa, _ainv, _res = _run_compute(
        monkeypatch, persist=False, builder_error=ValueError("missing_date_of_birth")
    )
    assert outcome.blocking_message is not None
    assert outcome.practical_result is None
