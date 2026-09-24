"""Rebalancing preference chat.

LIVE CONTRACT (ruling 2026-09-17): chat runs NO preference what-if. Every
preference-shaped ask relays `PREFERENCE_REDIRECT_MESSAGE` with the pill; a
first-turn or cash/tax-mixed ask serves the plan and lets the FORMATTER close
with the pointer. Saved preferences still shape plans and are still disclosed.

The S2 what-if tests below (candidate row, candidate run, save offer) cover the
UNREFERENCED re-enable seam, not live behaviour. Engine, formatter and DB seams
are spied."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.domains.rebalancing.services.rebal_engine.chat as chat_mod
from app.domains.rebalancing.tests.detector_ctx import make_detector_ctx

MIX = {"equity": 55.0, "debt": 35.0, "others": 10.0}
CANDIDATE_ID = uuid.UUID("c0ffee00-aaaa-4bbb-8ccc-ddddeeee0001")


def _applied(shortfall=None):
    return SimpleNamespace(preference_applied=True, shortfall_reason=shortfall)


def _practical(applied):
    """The PAA output the chat reads: the ACHIEVED mix is its own class
    breakdown (Task C2), not a field carried on human_override_applied."""
    return SimpleNamespace(
        human_override_applied=applied,
        asset_class_breakdown=SimpleNamespace(
            recommended=SimpleNamespace(
                equity_total_pct=64.0, debt_total_pct=28.0, others_total_pct=8.0,
            )
        ),
    )


def _outcome(applied=None):
    return SimpleNamespace(
        blocking_message=None,
        response=SimpleNamespace(
            kind="stub-response",
            practical_allocation=_practical(applied),
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
        practical_allocation=_practical(_applied()),
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
    """The extracted fields still beat the mode label — but since 2026-09-17
    they route to the preferences POINTER, not a what-if."""
    relayed = {}

    async def fake_relay(ctx, message, action_mode="redirect", show_preferences_pill=False):
        relayed.update(message=message, pill=show_preferences_pill)
        return chat_mod.ChatHandlerResult(
            text=message, snapshot_id=None, rebalancing_recommendation_id=None,
            show_preferences_pill=show_preferences_pill,
        )

    monkeypatch.setattr(chat_mod, "_relay", fake_relay)
    ctx = _ctx("more small cap")
    a = chat_mod.RebalanceAction(mode="consolidate", preference_asks=[_ask("small_cap")])

    await chat_mod._handle_action(ctx, a, None)

    assert relayed["pill"] is True
    assert relayed["message"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE


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


async def test_first_turn_preference_ask_computes_the_plan_and_points(monkeypatch):
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

    # A first-turn ask is MIXED intent: they want a plan. Since 2026-09-17 the
    # plain plan is computed and the pointer rides the FACTS PACK, so the
    # formatter writes the whole reply (never a concatenation).
    assert calls["whatif"] == [], "the what-if path is retired"
    assert len(calls["compute"]) == 1, "they asked for a plan — give them one"
    assert len(calls["format"]) == 1
    assert calls["format"][0]["preference_pointer"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE
    assert result.show_preferences_pill is True


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

    async def fake_compute(**kw):
        return _outcome()

    async def fake_format(**kw):
        return "formatted"

    monkeypatch.setattr(chat_mod, "_detect_rebal_action", spy_detect)
    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", fake_whatif)
    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)

    async def _resolved():
        return action

    task = asyncio.create_task(_resolved())
    await task

    ctx = _ctx("increase my equity exposure", speculative_detect=task)
    result = await chat_mod.handle(ctx)

    assert calls["serial_detect"] == 0, "speculative result was present; serial detect must not run"
    # The what-if is retired (2026-09-17): the ask now yields the plain plan
    # plus the preferences pointer.
    assert calls["whatif"] == []
    assert result.show_preferences_pill is True


# ---------------------------------------------------------------------------
# Disclosure: a plan shaped by a SAVED preference says so (Task 4)
# ---------------------------------------------------------------------------


def _saved_row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_choices={"class_mix": {}, "pins": []},
        asset_class_requested={"equity": 60.0, "debt": 30.0, "others": 10.0},
        resolved_targets={"high_beta_equities": 0.0},
    )


@pytest.fixture
def pack_spy(monkeypatch):
    """Capture the facts pack without running the formatter or the real builder."""
    seen = {}

    async def _fake_format(**kw):
        seen.update(kw)
        return "reply"

    monkeypatch.setattr(chat_mod, "format_with_telemetry", _fake_format)
    monkeypatch.setattr(chat_mod, "build_rebal_facts_pack", lambda *a, **k: dict(k))
    return seen


async def test_compute_turn_discloses_the_saved_preference(pack_spy):
    ctx = _ctx("rebalance me")
    ctx.user_ctx.saved_investment_preference = _saved_row()

    await chat_mod._format_or_fallback_rebal(
        ctx=ctx,
        response=_outcome(_applied()).response,
        fallback_brief="brief",
        action_mode="compute",
    )

    block = pack_spy["facts_pack"]["active_preferences"]
    assert block["applied"] is True
    assert block["choices"][0] == "60% equity / 30% debt / 10% commodity"
    assert "nothing in small-cap equity" in block["choices"]


async def test_a_what_if_turn_never_discloses_a_saved_preference(pack_spy):
    """constraint_impact means the applied override is an unsaved CANDIDATE —
    claiming a saved preference alongside it credits a save never made."""
    ctx = _ctx("make it 100% equity")
    ctx.user_ctx.saved_investment_preference = _saved_row()

    await chat_mod._format_or_fallback_rebal(
        ctx=ctx,
        response=_outcome(_applied()).response,
        fallback_brief="brief",
        action_mode="counterfactual_explore",
        constraint_impact={"save_offer": True},
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_a_plain_tax_counterfactual_stays_silent_too(pack_spy):
    """_counterfactual_explore passes constraint_impact=None explicitly, and the
    prompt has no disclosure rule for that mode."""
    ctx = _ctx("what if my tax rate were 20%?")
    ctx.user_ctx.saved_investment_preference = _saved_row()

    await chat_mod._format_or_fallback_rebal(
        ctx=ctx,
        response=_outcome(_applied()).response,
        fallback_brief="brief",
        action_mode="counterfactual_explore",
        constraint_impact=None,
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_no_row_means_no_block(pack_spy):
    ctx = _ctx("rebalance me")
    ctx.user_ctx.saved_investment_preference = None

    await chat_mod._format_or_fallback_rebal(
        ctx=ctx,
        response=_outcome(_applied()).response,
        fallback_brief="brief",
        action_mode="compute",
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_a_run_that_applied_nothing_means_no_block(pack_spy):
    ctx = _ctx("rebalance me")
    ctx.user_ctx.saved_investment_preference = _saved_row()

    await chat_mod._format_or_fallback_rebal(
        ctx=ctx,
        response=_outcome(None).response,  # human_override_applied is None
        fallback_brief="brief",
        action_mode="compute",
    )

    assert pack_spy["facts_pack"]["active_preferences"] is None


async def test_narrate_resolves_the_row_the_run_recorded(monkeypatch):
    """The customer saved preference B after this plan was built under A.
    Naming B for A's plan is a lie about their own instructions."""
    old_row = _saved_row()

    async def _fake_for_run(ctx, recommendation_id):
        return old_row

    monkeypatch.setattr(chat_mod, "_preference_row_for_run", _fake_for_run)

    ctx = _ctx("why is there no small cap?")
    ctx.user_ctx.saved_investment_preference = _saved_row()  # the NEW row
    last_run = SimpleNamespace(
        output_payload={"correlation_ids": {"recommendation_id": str(uuid.uuid4())}}
    )

    row = await chat_mod._preference_row_for_turn(ctx, last_run, "narrate")

    assert row is old_row, "a rehydrated turn resolves the row from the RUN"


async def test_a_fresh_turn_uses_the_active_row():
    ctx = _ctx("rebalance me")

    row = await chat_mod._preference_row_for_turn(ctx, None, "compute")

    assert row is chat_mod._FRESH


async def test_a_run_with_no_recommendation_id_discloses_nothing():
    ctx = _ctx("why is there no small cap?")
    last_run = SimpleNamespace(output_payload={"correlation_ids": {}})

    assert await chat_mod._preference_row_for_turn(ctx, last_run, "narrate") is None


async def test_a_failed_lookup_degrades_to_nothing(monkeypatch):
    async def _boom(ctx, recommendation_id):
        raise RuntimeError("db is unhappy")

    monkeypatch.setattr(chat_mod, "_preference_row_for_run", _boom)

    ctx = _ctx("why is there no small cap?")
    last_run = SimpleNamespace(
        output_payload={"correlation_ids": {"recommendation_id": str(uuid.uuid4())}}
    )

    assert await chat_mod._preference_row_for_turn(ctx, last_run, "narrate") is None


# ---------------------------------------------------------------------------
# Task 8: a request to CHANGE the stored record routes to the preferences
# screen. Chat reshapes plans; it never writes the record.
# ---------------------------------------------------------------------------


async def test_a_record_change_ask_routes_to_preferences(monkeypatch):
    relayed = {}

    async def _fake_relay(ctx, message, action_mode="redirect", show_preferences_pill=False):
        relayed.update(message=message, pill=show_preferences_pill)
        return chat_mod.ChatHandlerResult(
            text=message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
            show_preferences_pill=show_preferences_pill,
        )

    monkeypatch.setattr(chat_mod, "_relay", _fake_relay)

    action = chat_mod.RebalanceAction(
        mode="redirect", redirect_reason="change your saved preference"
    )
    result = await chat_mod._handle_action(ctx := _ctx("remove my small cap preference"),
                                          action, SimpleNamespace(output_payload={}))
    assert ctx is not None
    assert relayed["pill"] is True, "the customer needs a route to the record"
    assert result.show_preferences_pill is True
    assert "preferences" in relayed["message"].lower()


async def test_an_ordinary_redirect_still_points_at_profile(monkeypatch):
    relayed = {}

    async def _fake_relay(ctx, message, action_mode="redirect", show_preferences_pill=False):
        relayed.update(message=message, pill=show_preferences_pill)
        return chat_mod.ChatHandlerResult(text=message, snapshot_id=None,
                                          rebalancing_recommendation_id=None)

    monkeypatch.setattr(chat_mod, "_relay", _fake_relay)

    # NOT a "hold/keep/lock" reason — those hit the pre-existing
    # _LOCK_NOT_SUPPORTED branch, which is separate behaviour.
    action = chat_mod.RebalanceAction(
        mode="redirect", redirect_reason="defer this rebalance by 3 months"
    )
    await chat_mod._handle_action(_ctx("can I defer this by 3 months?"), action,
                                 SimpleNamespace(output_payload={}))

    assert relayed["pill"] is False, "only a RECORD ask offers the preferences route"
    assert "Profile" in relayed["message"] or "Holdings" in relayed["message"]


# ---------------------------------------------------------------------------
# 2026-09-17 ruling: chat runs NO preference what-ifs. Every preference-shaped
# ask — exposure, readout, change, undo — points at the preferences page.
# ---------------------------------------------------------------------------


@pytest.fixture
def relay_spy(monkeypatch):
    seen = {}

    async def _fake_relay(ctx, message, action_mode="redirect", show_preferences_pill=False):
        seen.update(message=message, pill=show_preferences_pill, mode=action_mode)
        return chat_mod.ChatHandlerResult(
            text=message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
            show_preferences_pill=show_preferences_pill,
        )

    monkeypatch.setattr(chat_mod, "_relay", _fake_relay)
    return seen


async def test_an_exposure_ask_points_at_preferences_instead_of_reshaping(
    relay_spy, monkeypatch
):
    """'I want more equity' used to reshape the plan and offer a save pill."""
    reshaped = {"called": 0}

    async def _never(ctx, action, last_run):
        reshaped["called"] += 1
        raise AssertionError("the what-if path must not run any more")

    monkeypatch.setattr(chat_mod, "_handle_preference_what_if", _never)

    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        preference_asks=[{"target": "equity", "level": "more"}],
    )
    result = await chat_mod._handle_action(
        _ctx("I want more equity"), action, SimpleNamespace(output_payload={})
    )

    assert reshaped["called"] == 0
    assert relay_spy["pill"] is True
    # "pointer", not the default "redirect": that body opens "You are relaying a
    # LIMIT", which brings back the apology the copy exists to avoid.
    assert relay_spy["mode"] == "pointer"
    assert relay_spy["message"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE
    assert "preferences page" in relay_spy["message"]
    assert result.show_preferences_pill is True
    assert result.rebalancing_recommendation_id is None, "nothing is persisted"


async def test_a_record_change_ask_gets_the_same_answer(relay_spy):
    """One consistent reply whether they ask to change, undo, or re-expose."""
    action = chat_mod.RebalanceAction(
        mode="redirect", redirect_reason="change your saved preference"
    )
    await chat_mod._handle_action(
        _ctx("remove my small cap preference"), action,
        SimpleNamespace(output_payload={}),
    )

    assert relay_spy["pill"] is True
    # "pointer", not the default "redirect": that body opens "You are relaying a
    # LIMIT", which brings back the apology the copy exists to avoid.
    assert relay_spy["mode"] == "pointer"
    assert relay_spy["message"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE


async def test_an_unmappable_ask_gets_the_same_answer(relay_spy):
    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        preference_asks=[
            {"target": "other", "level": "more", "other_words": "ESG funds"}
        ],
    )
    await chat_mod._handle_action(
        _ctx("more ESG funds"), action, SimpleNamespace(output_payload={})
    )

    assert relay_spy["pill"] is True
    # "pointer", not the default "redirect": that body opens "You are relaying a
    # LIMIT", which brings back the apology the copy exists to avoid.
    assert relay_spy["mode"] == "pointer"


# ---------------------------------------------------------------------------
# Review fixes 2026-09-17: the pointer goes through the FORMATTER (never string
# concat), and a mixed "cash + preference" ask still answers the cash half.
# ---------------------------------------------------------------------------


async def test_the_first_turn_pointer_goes_through_the_facts_pack(monkeypatch):
    """`answer_formatter` writes every customer-facing reply. Concatenating the
    pointer onto its output bypassed that contract."""
    seen = {}

    async def fake_compute(**kw):
        return _outcome()

    async def fake_detect(last_run, ctx):
        return chat_mod.RebalanceAction(
            mode="counterfactual_explore", preference_asks=[_ask("equity")]
        )

    async def fake_format(**kw):
        seen.update(kw)
        return "formatted reply"

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(chat_mod, "_detect_rebal_action", fake_detect)
    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)

    result = await chat_mod.handle(_cold_start_ctx("rebalance me, but more equity"))

    assert seen["preference_pointer"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE
    assert result.text == "formatted reply", (
        "the reply must be exactly what the formatter wrote — no appended text"
    )
    assert result.show_preferences_pill is True


async def test_a_mixed_cash_and_preference_ask_still_answers_the_cash_half(monkeypatch):
    """The detector prompt explicitly emits overrides ALONGSIDE preference_asks.
    Returning only the pointer dropped a question chat can answer."""
    seen = {"explore": [], "relay": 0}

    async def fake_explore(ctx, overrides, preference_pointer=None):
        seen["explore"].append((overrides, preference_pointer))
        return chat_mod.ChatHandlerResult(
            text="hypothetical", snapshot_id=None,
            rebalancing_recommendation_id=None, show_preferences_pill=True,
        )

    async def fake_relay(ctx, message, action_mode="redirect", show_preferences_pill=False):
        seen["relay"] += 1
        return chat_mod.ChatHandlerResult(text=message, snapshot_id=None,
                                         rebalancing_recommendation_id=None)

    monkeypatch.setattr(chat_mod, "_counterfactual_explore", fake_explore)
    monkeypatch.setattr(chat_mod, "_relay", fake_relay)

    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        overrides={"additional_cash_inr": 200000},
        preference_asks=[_ask("equity")],
    )
    result = await chat_mod._handle_action(
        _ctx("what if I had 2L more — and more equity?"), action,
        SimpleNamespace(output_payload={}),
    )

    assert seen["relay"] == 0, "the cash question must not be swallowed"
    assert len(seen["explore"]) == 1
    overrides, pointer = seen["explore"][0]
    assert overrides == {"additional_cash_inr": 200000}
    assert pointer == chat_mod.PREFERENCE_REDIRECT_MESSAGE
    assert result.show_preferences_pill is True


async def test_a_preference_ask_with_no_servable_override_still_just_points(relay_spy):
    """An unsupported override key must not resurrect the engine run."""
    action = chat_mod.RebalanceAction(
        mode="counterfactual_explore",
        overrides={"defer_months": 3},
        preference_asks=[_ask("equity")],
    )
    await chat_mod._handle_action(
        _ctx("defer 3 months and more equity"), action,
        SimpleNamespace(output_payload={}),
    )

    assert relay_spy["pill"] is True
    # "pointer", not the default "redirect": that body opens "You are relaying a
    # LIMIT", which brings back the apology the copy exists to avoid.
    assert relay_spy["mode"] == "pointer"
    assert relay_spy["message"] == chat_mod.PREFERENCE_REDIRECT_MESSAGE


def test_the_facts_pack_itself_enforces_the_mutual_exclusion():
    """The chat seam nulls one of them, but the pack's `elif` is the invariant's
    real home — without this, that half can be deleted with the suite green."""
    from app.domains.rebalancing.services.rebal_engine.service import (
        build_rebal_facts_pack,
    )

    response = _outcome(_applied()).response
    block = {"choices": ["60% equity"], "applied": True, "shortfall_reason": None}

    both = build_rebal_facts_pack(
        response,
        constraint_impact={"save_offer": True},
        active_preferences=block,
    )
    assert "constraint_impact" in both
    assert "active_preferences" not in both, (
        "a candidate's contrast and a SAVED-preference disclosure must never "
        "ship together — that credits the customer with a save never made"
    )

    saved_only = build_rebal_facts_pack(response, active_preferences=block)
    assert saved_only["active_preferences"] == block
