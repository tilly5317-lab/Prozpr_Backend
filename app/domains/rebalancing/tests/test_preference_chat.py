"""S2 chat wiring: a preference ask runs as a one-off through the S1 channel,
persists a candidate row + candidate run, offers to save; "yes, save it"
activates that exact candidate. Engine, formatter, and DB seams are spied."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.rebalancing.services.rebal_engine.chat as chat_mod
from app.domains.rebalancing.tests.detector_ctx import make_detector_ctx

MIX = {"equity": 55.0, "debt": 35.0, "others": 10.0}
CANDIDATE_ID = uuid.UUID("c0ffee00-aaaa-4bbb-8ccc-ddddeeee0001")


def _applied(shortfall=None):
    return SimpleNamespace(
        requested={"equity": 65.0, "debt": 27.0, "others": 8.0},
        achieved={"equity": 64.0, "debt": 28.0, "others": 8.0},
        shortfall_reason=shortfall,
    )


def _outcome(applied=None):
    return SimpleNamespace(
        blocking_message=None,
        response=SimpleNamespace(
            kind="stub-response",
            practical_allocation=SimpleNamespace(human_override_applied=applied),
            subgroups=[],
            model_dump=lambda mode=None: {"totals": {}},
        ),
        formatted_text="brief",
        goal_buckets=None,
        allocation_snapshot_id=None,
        recommendation_id=None,
        source_allocation_id=uuid.uuid4(),
        used_cached_allocation=False,
    )


def _user():
    return SimpleNamespace(id=uuid.uuid4(), risk_profile=None, first_name="A")


def _ctx(question, **kw):
    import dataclasses

    ctx = make_detector_ctx(question, user_ctx=_user())
    return dataclasses.replace(ctx, db=object(), **kw)


@pytest.fixture
def spy(monkeypatch):
    calls = {"compute": [], "format": [], "relay": [], "persist": [], "module_runs": [],
             "telemetry": [], "resolve": [], "resolve_base": [], "insert": [],
             "confirm": []}
    resolved = SimpleNamespace(
        asset_class_requested={"equity": 65.0, "debt": 27.0, "others": 8.0},
        subgroup_emphasis={}, applied_defaults={"asset_class": "+10pp default"},
    )
    state = {"changed": {"asset_class": {"class": "equity", "direction": "more"}},
             "applied": _applied()}

    async def fake_compute(**kw):
        calls["compute"].append(kw)
        return _outcome(state["applied"] if kw.get("chat_ctx") and kw["chat_ctx"].chat_overrides else None)

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    async def fake_relay(**kw):
        calls["relay"].append(kw)
        return kw.get("message", "")

    async def fake_persist(db, user_id, response, **kw):
        calls["persist"].append({"response": response, **kw})
        return "candidate-run-id"

    async def fake_record(db, **kw):
        calls["module_runs"].append(kw)
        return "module-run-id"

    async def fake_resolve(db, user, chat_intent, *, base_row=None):
        calls["resolve"].append(chat_intent)
        calls["resolve_base"].append(base_row)
        return chat_intent, resolved, state["changed"]

    async def fake_insert(db, user, intent, res, achieved, shortfall_reason=None):
        calls["insert"].append({"intent": intent, "achieved": achieved})
        return SimpleNamespace(id=CANDIDATE_ID)

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)
    monkeypatch.setattr(chat_mod, "format_relay_or_canned", fake_relay)
    monkeypatch.setattr(chat_mod, "persist_rebalancing_recommendation", fake_persist)
    monkeypatch.setattr(chat_mod, "record_ai_module_run", fake_record)
    monkeypatch.setattr(chat_mod, "capture_preference_unserved",
                        lambda **kw: calls["telemetry"].append(kw))
    monkeypatch.setattr(chat_mod, "_current_target_mix_pct", lambda response: MIX)
    monkeypatch.setattr(chat_mod, "build_fallback_rebal_brief", lambda *a, **k: "brief")
    monkeypatch.setattr(chat_mod.prefs, "resolve_one_off", fake_resolve)
    monkeypatch.setattr(chat_mod.prefs, "insert_candidate", fake_insert)
    calls["state"] = state
    return calls


def _ask(target, level="more", number=None, other_words=None):
    return {"target": target, "level": level, "number": number, "other_words": other_words}


async def _async(value):
    return value


def _run_with_candidate(candidate_id=CANDIDATE_ID):
    from datetime import datetime, timezone

    from app.domains.ai_engine.turn_context import AgentRunRecord

    return AgentRunRecord(
        id=uuid.uuid4(), module="rebalancing", intent_detected="rebalancing",
        input_payload=None,
        output_payload={"rebalancing_response": {}, "goal_buckets": None,
                        "correlation_ids": {"recommendation_id": "x",
                                            "candidate_preference_id": str(candidate_id)}},
        created_at=datetime.now(timezone.utc),
    )


async def test_what_if_runs_baseline_and_one_off_through_the_s1_channel(spy):
    ctx = _ctx("increase my equity exposure")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity")])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert spy["resolve"] == [{"asset_class": {"class": "equity", "direction": "more"}}]
    assert len(spy["compute"]) == 2
    assert spy["compute"][0]["persist"] is False and spy["compute"][1]["persist"] is False
    ov = spy["compute"][1]["chat_ctx"].chat_overrides
    assert ov["human_override_preferences"] == {
        "asset_class_requested": {"equity": 65.0, "debt": 27.0, "others": 8.0},
        "subgroup_emphasis": {},
    }


async def test_what_if_persists_candidate_row_then_candidate_run_fk_to_it(spy):
    ctx = _ctx("increase my equity exposure")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity")])
    result = await chat_mod._handle_preference_what_if(ctx, action, None)
    assert spy["insert"][0]["achieved"] == {"equity": 64.0, "debt": 28.0, "others": 8.0}
    p = spy["persist"][0]
    assert p["origin"] == chat_mod.ORIGIN_CANDIDATE
    assert p["saved_investment_preference_id"] == CANDIDATE_ID
    out = spy["module_runs"][0]["output_payload"]
    assert out["correlation_ids"]["candidate_preference_id"] == str(CANDIDATE_ID)
    assert out["correlation_ids"]["recommendation_id"] == "candidate-run-id"
    assert result.rebalancing_recommendation_id == "candidate-run-id"
    # A what-if produced a savable candidate → the pill notes the preference is
    # kept on save and shows "View preferences" (ordinary turns leave this False).
    assert result.has_candidate_preference is True


async def test_what_if_facts_carry_shortfall_and_the_save_offer(spy):
    spy["state"]["applied"] = _applied(shortfall="emergency buffer reduced by 30%")
    ctx = _ctx("make it 100% equity")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity", "number", 100)])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    impact = spy["format"][0]["constraint_impact"]
    assert spy["format"][0]["action_mode"] == "counterfactual_explore"
    assert impact["preference_shortfall"] == "emergency buffer reduced by 30%"
    assert impact["save_offer"] is True
    assert impact["recommended_mix_pct"] == MIX and "requested_mix_pct" in impact
    assert impact["applied_preferences"] == {"customer_choices": {
        "asset_class": {"class": "equity", "direction": "target", "target_pct": 100.0}
    }}
    assert "just this once" not in impact["tilt_note"].lower()

    spy["state"]["applied"] = _applied()
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert "preference_shortfall" not in spy["format"][1]["constraint_impact"]


async def test_partially_unmapped_ask_runs_the_mapped_part_and_discloses_the_rest(spy):
    ctx = _ctx("more equity and only banking funds")
    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        preference_asks=[_ask("equity"), _ask("other", "more", other_words="banking funds")],
    )
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert len(spy["compute"]) == 2, "the mapped ask still runs"
    assert spy["telemetry"][0]["failure_class"] == "unmapped_category"
    impact = spy["format"][0]["constraint_impact"]
    assert impact["applied_preferences"]["not_applied"] == ["banking funds"]


async def test_subgroup_ask_adds_the_category_contrast(spy, monkeypatch):
    monkeypatch.setattr(chat_mod, "_equity_subgroup_mix_pct",
                        lambda response: {"low_beta_equities": 50.0, "high_beta_equities": 50.0})
    spy["state"]["changed"] = {"subgroups": {"high_beta_equities": "heavy"}}
    ctx = _ctx("small-cap heavy")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("small_cap", "heavy")])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    impact = spy["format"][0]["constraint_impact"]
    assert "recommended_category_mix_pct" in impact and "requested_category_mix_pct" in impact
    assert "category" in impact["tilt_note"].lower()


async def test_gold_ask_skips_the_equity_only_category_contrast(spy, monkeypatch):
    # gold_commodities is not equity-class: the asset-class mix IS the move, so
    # the equity-split contrast (and its "do NOT lead with it" directive) must not fire.
    monkeypatch.setattr(chat_mod, "_equity_subgroup_mix_pct",
                        lambda response: {"low_beta_equities": 60.0, "high_beta_equities": 40.0})
    spy["state"]["changed"] = {"subgroups": {"gold_commodities": "more"}}
    ctx = _ctx("add some gold")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("gold")])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    impact = spy["format"][0]["constraint_impact"]
    assert "recommended_category_mix_pct" not in impact
    assert "requested_category_mix_pct" not in impact
    assert "CATEGORIES" not in impact["tilt_note"]


async def test_empty_equity_split_skips_the_category_contrast(spy, monkeypatch):
    # Never hand the formatter an empty dict to quote verbatim.
    monkeypatch.setattr(chat_mod, "_equity_subgroup_mix_pct", lambda response: {})
    spy["state"]["changed"] = {"subgroups": {"high_beta_equities": "heavy"}}
    ctx = _ctx("small-cap heavy")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("small_cap", "heavy")])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    impact = spy["format"][0]["constraint_impact"]
    assert "recommended_category_mix_pct" not in impact
    assert "CATEGORIES" not in impact["tilt_note"]


async def test_unmapped_ask_declines_honestly_with_token_only_telemetry(spy):
    ctx = _ctx("only banking funds please")
    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        preference_asks=[_ask("other", "more", other_words="banking funds")],
    )
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert spy["compute"] == [] and spy["insert"] == []
    assert spy["telemetry"] == [dict(flow="rebalancing", failure_class="unmapped_category",
                                     session_id=ctx.session_id, distinct_id=ctx.effective_user_id)]
    assert spy["relay"] and "banking funds" in spy["relay"][0]["message"]


async def test_ask_identical_to_saved_preference_short_circuits(spy):
    spy["state"]["changed"] = {}
    ctx = _ctx("more equity")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity")])
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert spy["compute"] == [] and spy["insert"] == []
    assert spy["relay"] and "already" in spy["relay"][0]["message"].lower()


async def test_tax_override_rides_alongside_the_preference(spy):
    ctx = _ctx("more equity with tax at 20%")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity")],
                                      overrides={"effective_tax_rate": 20})
    await chat_mod._handle_preference_what_if(ctx, action, None)
    ov = spy["compute"][1]["chat_ctx"].chat_overrides
    assert ov["effective_tax_rate"] == 20 and "human_override_preferences" in ov


async def test_fund_count_reshapes_the_requested_plan_before_persisting(spy, monkeypatch):
    reshaped = SimpleNamespace(
        totals=SimpleNamespace(funds_to_buy_count=4), kind="reshaped",
        practical_allocation=SimpleNamespace(human_override_applied=_applied()),
        subgroups=[], model_dump=lambda mode=None: {"totals": {"funds_to_buy_count": 4}},
    )
    monkeypatch.setattr("Rebalancing.consolidation.reshape_response",
                        lambda response, constraints, **kw: (reshaped, None))
    ctx = _ctx("only equity, max 4 funds")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("equity", "number", 100)],
                                      target_fund_count=4)
    await chat_mod._handle_preference_what_if(ctx, action, None)
    assert spy["persist"][0]["response"] is reshaped
    assert spy["format"][0]["response"] is reshaped
    assert spy["format"][0]["constraint_impact"]["applied_preferences"]["fund_count"] == 4


async def test_dispatch_routes_preference_asks_over_the_mode_label(monkeypatch):
    called = {}

    async def fake_what_if(ctx, action, last_run):
        called["what_if"] = (action, last_run)
        return "WHATIF"

    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_what_if)
    ctx = _ctx("more small cap")
    a = chat_mod.RebalanceAction(mode="consolidate", preference_asks=[_ask("small_cap")])
    assert await chat_mod._handle_action(ctx, a, None) == "WHATIF"   # fields beat the mode label
    assert called["what_if"][1] is None


async def test_chained_what_if_composes_over_the_live_candidate(spy, monkeypatch):
    """S2 ruling 15: the follow-up merges over the candidate the customer just
    saw, not the saved row — otherwise the save offer names a plan they never saw."""
    stub = SimpleNamespace(id=CANDIDATE_ID, is_active=False, activated_at=None)
    monkeypatch.setattr(chat_mod.prefs, "candidate_row",
                        lambda db, user_id, cid: _async(stub))
    ctx = _ctx("add some gold to that")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("gold")])
    await chat_mod._handle_preference_what_if(ctx, action, _run_with_candidate())
    assert spy["resolve_base"] == [stub]


async def test_chained_what_if_ignores_an_already_saved_candidate(spy, monkeypatch):
    from datetime import datetime, timezone

    stub = SimpleNamespace(id=CANDIDATE_ID, is_active=True,
                           activated_at=datetime.now(timezone.utc))
    monkeypatch.setattr(chat_mod.prefs, "candidate_row",
                        lambda db, user_id, cid: _async(stub))
    ctx = _ctx("add some gold to that")
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      preference_asks=[_ask("gold")])
    await chat_mod._handle_preference_what_if(ctx, action, _run_with_candidate())
    assert spy["resolve_base"] == [None]


async def test_compute_mode_selects_the_confirm_body(monkeypatch):
    from app.domains.ai_engine.answer_formatter import formatter as fmt_mod

    captured = {}

    async def fake_fmt(*, ctx, facts_pack, body_prompt, module_name,
                       action_mode, profile, build_fallback):
        captured.update(body_prompt=body_prompt, action_mode=action_mode)
        return "TAILORED"

    monkeypatch.setattr(fmt_mod, "format_with_telemetry", fake_fmt)
    out = await fmt_mod.format_relay_or_canned(
        ctx=_ctx("save it"), module_name="rebalancing", message="m",
        action_mode="compute",
    )
    assert out == "TAILORED"
    assert captured["action_mode"] == "compute"
    assert captured["body_prompt"] is fmt_mod._CONFIRM_BODY


# ---------------------------------------------------------------------------
# S2c Task 5: cold start — a preference ask on the very first turn is a
# what-if on the plan just computed, not the ordinary compute-and-format path.
# ---------------------------------------------------------------------------


def _cold_start_ctx(question):
    return _ctx(question)


async def test_first_turn_preference_ask_runs_what_if_before_any_compute(monkeypatch):
    calls = {"whatif": [], "format": [], "compute": []}

    async def fake_compute(**kw):
        calls["compute"].append(kw)
        return _outcome()

    async def fake_detect(last_run, ctx):
        return chat_mod.RebalanceAction(
            mode="counterfactual_explore", preference_asks=[_ask("equity")]
        )

    async def fake_whatif(ctx, action, last_run):
        calls["whatif"].append((ctx, action, last_run))
        return "WHATIF"

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_detect_rebal_action", fake_detect)
    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_whatif)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)

    ctx = _cold_start_ctx("increase my equity exposure")
    result = await chat_mod.handle(ctx)

    assert result == "WHATIF"
    assert calls["format"] == []
    assert calls["compute"] == [], "handle() must not compute a plain plan when asks are found"
    assert len(calls["whatif"]) == 1
    whatif_ctx, whatif_action, whatif_last_run = calls["whatif"][0]
    assert whatif_ctx is ctx
    assert whatif_action.preference_asks[0].target == "equity"
    assert whatif_last_run is None


async def test_first_turn_no_preference_asks_uses_ordinary_formatting(monkeypatch):
    calls = {"whatif": [], "format": [], "compute": []}

    async def fake_compute(**kw):
        calls["compute"].append(kw)
        return _outcome()

    async def fake_detect(last_run, ctx):
        return chat_mod.RebalanceAction(mode="narrate")

    async def fake_whatif(ctx, action, last_run):
        calls["whatif"].append(1)
        return "WHATIF"

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_detect_rebal_action", fake_detect)
    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_whatif)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)

    ctx = _cold_start_ctx("what should I do")
    result = await chat_mod.handle(ctx)

    assert calls["whatif"] == []
    assert len(calls["compute"]) == 1
    assert len(calls["format"]) == 1
    assert result.text == "formatted"


async def test_first_turn_detector_error_falls_back_to_ordinary_formatting(monkeypatch):
    calls = {"whatif": [], "format": [], "compute": []}

    async def fake_compute(**kw):
        calls["compute"].append(kw)
        return _outcome()

    async def fake_detect(last_run, ctx):
        raise RuntimeError("classifier boom")

    async def fake_whatif(ctx, action, last_run):
        calls["whatif"].append(1)
        return "WHATIF"

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_detect_rebal_action", fake_detect)
    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_whatif)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)

    ctx = _cold_start_ctx("increase my equity exposure")
    result = await chat_mod.handle(ctx)

    assert calls["whatif"] == []
    assert len(calls["compute"]) == 1
    assert len(calls["format"]) == 1
    assert result.text == "formatted"


# ---------------------------------------------------------------------------
# S2d Task 5: the brain's speculative detect now covers a first rebalancing
# turn too (ai_engine/services/brain.py). handle() must CONSUME that result
# instead of re-running _detect_rebal_action serially — one detector call
# total, never both the speculative one and a serial one.
# ---------------------------------------------------------------------------


async def test_first_turn_consumes_speculative_detect_without_serial_call(monkeypatch):
    """Speculative result already present on a first turn -> no serial detect."""
    import asyncio

    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore", preference_asks=[_ask("equity")]
    )
    calls = {"serial_detect": 0, "whatif": []}

    async def spy_detect(last_run, ctx):
        calls["serial_detect"] += 1
        return action

    async def fake_whatif(ctx, action_arg, last_run):
        calls["whatif"].append((action_arg, last_run))
        return "WHATIF"

    monkeypatch.setattr(chat_mod, "_detect_rebal_action", spy_detect)
    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_whatif)

    async def _resolved():
        return action

    task = asyncio.create_task(_resolved())
    await task

    ctx = _ctx("increase my equity exposure", speculative_detect=task)
    result = await chat_mod.handle(ctx)

    assert calls["serial_detect"] == 0, "speculative result was present; serial detect must not run"
    assert result == "WHATIF"
    assert calls["whatif"] == [(action, None)]
