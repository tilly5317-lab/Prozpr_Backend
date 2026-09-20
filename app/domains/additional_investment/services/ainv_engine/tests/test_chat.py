"""Mirror of rebalancing's @register lock test, plus ainv handler/facts smoke tests.

BUY-only / write-once intent: the handler always recomputes in `compute` mode,
so the tests cover the register side-effect, the facts pack naming the buy funds,
the under-deploy nudge, SIP monthly amounts, the deterministic fund-naming
fallback, and the first-turn handler path. All stand-ins are plain fakes — no
real DB or LLM.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.ai_engine.chat_dispatcher import _HANDLERS
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from additional_investment.models import (  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
    FundBuy,
    SubgroupTarget,
    TargetBucket,
)


def _output(
    *, cadence: Cadence = Cadence.LUMPSUM, undeployed: float = 0.0
) -> AdditionalInvestmentOutput:
    sip = cadence == Cadence.SIP_MONTHLY
    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=cadence,
        deploy_amount_inr=100000.0,
        deployed_inr=100000.0 - undeployed,
        undeployed_inr=undeployed,
        per_subgroup_target=[
            SubgroupTarget(subgroup="low_beta_equities", ratio=0.6, target_inr=60000.0),
            SubgroupTarget(subgroup="short_debt", ratio=0.4, target_inr=40000.0),
        ],
        buys=[
            FundBuy(
                recommended_fund="HDFC Top 100",
                isin="INF111",
                sub_category="Large Cap Fund",
                asset_subgroup="low_beta_equities",
                amount_inr=60000.0,
                monthly_amount_inr=5000.0 if sip else None,
                reason="Recommended fund for this category",
            ),
            FundBuy(
                recommended_fund="ICICI Ultra Short",
                isin="INF222",
                sub_category="Ultra Short Duration Fund",
                asset_subgroup="short_debt",
                amount_inr=40000.0,
                monthly_amount_inr=3000.0 if sip else None,
                reason="Recommended fund for this category",
            ),
        ],
    )


def _ctx(question: str = "invest 1 lakh", *, last_run=None, user_ctx=None) -> TurnContext:
    last_runs = {"additional_investment": last_run} if last_run else {}
    return TurnContext(
        user_ctx=user_ctx
        or MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
        active_intent="additional_investment",
        awaiting_save=False,
    )


def test_register_side_effect_for_additional_investment():
    """Importing ainv_engine.chat must register the 'additional_investment' handler."""
    import app.domains.additional_investment.services.ainv_engine.chat as mod

    importlib.reload(mod)
    assert (
        "additional_investment" in _HANDLERS
    ), "@register('additional_investment') side-effect missing"


def test_facts_pack_carries_buy_fund_names():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output())
    names = [b["recommended_fund"] for b in facts["buys"]]
    assert "HDFC Top 100" in names
    assert "ICICI Ultra Short" in names
    assert facts["deploy_amount_indian"]  # non-empty formatted string
    assert facts["cadence"] == "lumpsum"
    assert facts["target_bucket"] == "long_term"
    assert [t["subgroup"] for t in facts["per_subgroup_target"]] == [
        "low_beta_equities",
        "short_debt",
    ]
    # No leftover → no under-deploy nudge.
    assert facts["undeployed_inr"] == 0.0
    assert "under_deploy_note" not in facts


def test_facts_pack_adds_under_deploy_note_when_leftover():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(undeployed=15000.0))
    assert facts["undeployed_inr"] == 15000.0
    assert "under_deploy_note" in facts
    assert facts["under_deploy_note"]  # non-empty one-liner


def test_facts_pack_sip_carries_monthly_amounts():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(cadence=Cadence.SIP_MONTHLY))
    assert facts["cadence"] == "sip_monthly"
    assert all(b["monthly_amount_inr"] is not None for b in facts["buys"])
    assert all(b["monthly_amount_indian"] for b in facts["buys"])
    # Finding 6: SIP buys carry ONLY the per-month figure (one-time amount nulled).
    assert all(b["amount_inr"] is None for b in facts["buys"])
    assert all(b["amount_indian"] is None for b in facts["buys"])


def test_build_fallback_brief_names_funds():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_output())
    assert "HDFC Top 100" in brief
    assert "ICICI Ultra Short" in brief


def test_build_fallback_brief_sip_has_single_monthly_column():
    """Finding 6: a SIP brief shows one 'Monthly' column, not Monthly + One-time
    (which would print the identical figure twice)."""
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_output(cadence=Cadence.SIP_MONTHLY))
    assert "| Buy into | Monthly |" in brief
    assert "One-time" not in brief
    assert "HDFC Top 100" in brief


def test_handle_runs_engine_and_calls_formatter():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=_output(),
        run_id=uuid.uuid4(),
    )
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(100000.0, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.format_answer",
            new=AsyncMock(return_value="tailored ainv answer"),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx()))
    assert result.text == "tailored ainv answer"


def test_ordinary_deploy_surfaces_cadence_but_not_run_id():
    """An ordinary deploy surfaces its cadence so the chat 'View plan' button can
    open the matching SIP/lump-sum popup — while still withholding the run id, so
    'Save preference' (gated on the run id) stays off on a plain deploy."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=_output(cadence=Cadence.LUMPSUM),
        run_id=uuid.uuid4(),
    )
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(100000.0, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.format_answer",
            new=AsyncMock(return_value="tailored ainv answer"),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx()))
    assert result.additional_investment_cadence == "lumpsum"
    assert result.additional_investment_run_id is None


def test_handle_falls_back_to_fund_naming_brief_on_formatter_failure():
    from app.domains.ai_engine.answer_formatter import FormatterFailure
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=_output(),
        run_id=None,
    )
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(100000.0, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.format_answer",
            new=AsyncMock(side_effect=FormatterFailure("api_down")),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx()))
    # Formatter failed → deterministic fallback brief, which names the buy funds.
    assert "HDFC Top 100" in result.text
    assert "ICICI Ultra Short" in result.text


def test_handle_asks_for_amount_when_question_has_no_number():
    """No parseable amount → clarify reply via the shared relay; the orchestrator
    is never called."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(None, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat,
            "format_relay_or_canned",
            new=AsyncMock(return_value="how much, and lumpsum or SIP?"),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("where should I put my money?")))

    assert result.text == "how much, and lumpsum or SIP?"
    compute.assert_not_awaited()


def test_handle_relays_blocking_message_instead_of_buys():
    """A blocking outcome (output None, blocking_message set) is relayed via the
    shared relay rather than formatted as a BUY list."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=None,
        blocking_message="I need your date of birth before I can plan this.",
    )
    relay = AsyncMock(return_value="relayed gate text")
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(100000.0, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch.object(ainv_chat, "format_relay_or_canned", new=relay),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("invest 1 lakh")))

    assert result.text == "relayed gate text"
    relay.assert_awaited_once()
    assert relay.await_args.kwargs["module_name"] == "additional_investment"
    assert relay.await_args.kwargs["message"] == (
        "I need your date of birth before I can plan this."
    )


def test_extract_deploy_request_lumpsum():
    """LLM extraction maps the structured Haiku result to (amount, Cadence, category)."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    with patch.object(
        ainv_chat,
        "classify_action",
        new=AsyncMock(
            return_value=ainv_chat._DeployRequest(amount_inr=500000.0, cadence="lumpsum")
        ),
    ):
        amount, cadence, raw_category, _asks = asyncio.run(ainv_chat.extract_deploy_request("invest 5L"))
    assert amount == 500000.0
    assert cadence == Cadence.LUMPSUM
    assert raw_category is None


def test_extract_deploy_request_sip():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    with patch.object(
        ainv_chat,
        "classify_action",
        new=AsyncMock(
            return_value=ainv_chat._DeployRequest(
                amount_inr=25000.0, cadence="sip_monthly"
            )
        ),
    ):
        amount, cadence, raw_category, _asks = asyncio.run(ainv_chat.extract_deploy_request("start a 25k SIP"))
    assert amount == 25000.0
    assert cadence == Cadence.SIP_MONTHLY
    assert raw_category is None


def test_extract_falls_back_to_regex_on_llm_failure():
    """A failed Haiku call falls back to the deterministic regex parser."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    with patch.object(
        ainv_chat,
        "classify_action",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        amount, cadence, raw_category, _asks = asyncio.run(
            ainv_chat.extract_deploy_request("start a 25k monthly SIP")
        )
    # Regex fallback parsed the amount + SIP cadence; no category from regex.
    assert amount == 25000.0
    assert cadence == Cadence.SIP_MONTHLY
    assert raw_category is None


def _empty_output() -> AdditionalInvestmentOutput:
    """Targeted horizon bucket had no eligible funds — nothing deployed."""
    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.LUMPSUM,
        deploy_amount_inr=100000.0,
        deployed_inr=0.0,
        undeployed_inr=100000.0,
        per_subgroup_target=[],
        buys=[],
    )


def test_facts_pack_suppresses_under_deploy_note_for_rounding_crumb():
    """A sub-threshold remainder (the ₹100 rounding crumb a split leaves) is NOT
    surfaced as a shortfall — only material undeploys get the note."""
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(undeployed=150.0))
    assert facts["undeployed_inr"] == 150.0
    assert "under_deploy_note" not in facts


def test_facts_pack_empty_buys_gets_nothing_placed_note():
    """Nothing deployed (empty bucket) → a distinct 'couldn't place any' note, not
    the per-fund-cap shortfall wording."""
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_empty_output())
    assert facts["buys"] == []
    assert "under_deploy_note" in facts
    assert "couldn't place any" in facts["under_deploy_note"]


def test_build_fallback_brief_empty_buys_has_no_table():
    """The deterministic fallback renders no buy table when nothing was deployed."""
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_empty_output())
    assert "| Buy into |" not in brief
    assert "couldn't place any" in brief


def test_handle_asks_for_amount_when_amount_is_zero():
    """A zero/negative amount is rejected before the engine (Field gt=0) — the
    handler asks for the amount instead of crashing (audit Finding 3)."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(0.0, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat,
            "format_relay_or_canned",
            new=AsyncMock(return_value="how much, and lumpsum or SIP?"),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("invest 0")))

    assert result.text == "how much, and lumpsum or SIP?"
    compute.assert_not_awaited()


# ── deficit-fill facts + body prompts (spec 2026-07-03) ─────────────────────
def test_facts_pack_carries_deficit_rows_when_provided():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    rows = [{"subgroup": "low_beta_equities", "ideal_inr": 150000.0,
             "current_inr": 100000.0, "gap_inr": 50000.0, "buy_inr": 50000.0}]
    facts = build_ainv_facts_pack(_output(), deficit_rows=rows)
    assert facts["deficit_rows"] == rows


def test_facts_pack_omits_deficit_rows_by_default():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output())
    assert "deficit_rows" not in facts


def test_legacy_body_is_sip_only_and_deficit_body_exists():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod

    assert "lumpsum" not in chat_mod._AINV_FORMATTER_BODY.lower()
    assert "gap" in chat_mod._AINV_DEFICIT_FORMATTER_BODY.lower()
    assert "emergency" in chat_mod._AINV_DEFICIT_FORMATTER_BODY.lower()


# ── extractor: focus_category + history (spec 2026-07-04) ───────────────────
def test_extract_returns_category_and_includes_history_block():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    seen = {}

    async def _fake_classify(**kwargs):
        seen.update(kwargs)
        return ainv_chat._DeployRequest(
            amount_inr=500000.0, cadence="lumpsum", focus_category="smallcap"
        )

    history = [
        {"role": "user", "content": "I am looking to invest around 5 lakhs"},
        {"role": "assistant", "content": "Here's the plan..."},
    ]
    with (
        patch.object(ainv_chat, "classify_action", new=_fake_classify),
        patch.object(ainv_chat, "get_settings"),  # hermetic: no .env dependence
    ):
        amount, cadence, raw, _asks = asyncio.run(
            ainv_chat.extract_deploy_request("smallcap funds only", history)
        )

    assert (amount, cadence.value, raw) == (500000.0, "lumpsum", "smallcap")
    assert "Recent Conversation History" in seen["user_block"]
    assert "5 lakhs" in seen["user_block"]
    assert "smallcap funds only" in seen["user_block"]
    assert seen["max_tokens"] == 200


def test_extract_regex_fallback_has_no_category():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    async def _boom(**kwargs):
        raise RuntimeError("llm down")

    with (
        patch.object(ainv_chat, "classify_action", new=_boom),
        patch.object(ainv_chat, "get_settings"),  # hermetic: no .env dependence
    ):
        amount, cadence, raw, _asks = asyncio.run(
            ainv_chat.extract_deploy_request("invest 50k as lumpsum", None)
        )

    assert amount == 50000.0
    assert raw is None


# ── category_ask facts + prompts (spec 2026-07-04) ──────────────────────────
def _category_ask(status="in_plan", category="Small Cap Fund"):
    return {
        "asked_text": "smallcap",
        "category": category,
        "status": status,
        "top_funds": [
            {"fund": "Nippon India Small Cap Fund", "sub_category": "Small Cap Fund"},
            {"fund": "Bandhan Small Cap Fund", "sub_category": "Small Cap Fund"},
        ],
        "subgroup_ideal_inr": 100000.0,
        "subgroup_current_inr": 150000.0,
    }


def test_facts_pack_carries_category_ask_when_provided():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(), category_ask=_category_ask())
    assert facts["category_ask"]["category"] == "Small Cap Fund"


def test_facts_pack_omits_category_ask_by_default():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    assert "category_ask" not in build_ainv_facts_pack(_output())


def test_fallback_brief_appends_category_line():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_output(), category_ask=_category_ask())
    assert "Nippon India Small Cap Fund" in brief
    assert "recommend the plan above" in brief


def test_fallback_brief_handles_not_ranked_category():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    ask = _category_ask(status="not_ranked", category=None)
    ask["top_funds"] = []
    brief = _build_fallback_ainv_brief(_output(), category_ask=ask)
    assert "don't have ranked funds" in brief


def test_probe_body_and_fallback_exist():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod

    assert "category_ask" in chat_mod._AINV_CATEGORY_PROBE_BODY
    assert "caveat" in chat_mod._AINV_CATEGORY_PROBE_BODY.lower() or \
           "recommend" in chat_mod._AINV_CATEGORY_PROBE_BODY.lower()
    probe = chat_mod._build_fallback_category_probe(_category_ask())
    assert "Nippon India Small Cap Fund" in probe
    assert "how much" in probe.lower()


def test_both_plan_bodies_document_category_ask():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod

    assert "category_ask" in chat_mod._AINV_DEFICIT_FORMATTER_BODY
    assert "category_ask" in chat_mod._AINV_FORMATTER_BODY
    assert "never replaces it" in chat_mod._AINV_DEFICIT_FORMATTER_BODY
    assert "never replaces it" in chat_mod._AINV_FORMATTER_BODY


# ── handler routing: category branches (spec 2026-07-04) ────────────────────
def _patch_probe_format():
    """Patch the shared formatter used by the probe path; returns the mock."""
    return patch(
        "app.domains.ai_engine.answer_formatter.formatter.format_answer",
        new=AsyncMock(return_value="probe reply"),
    )


def test_category_without_amount_probes_and_skips_compute():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(None, Cadence.LUMPSUM, "smallcap", None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat, "resolve_category", new=lambda t: "Small Cap Fund"
        ),
        patch.object(
            ainv_chat,
            "top_funds_for_category",
            new=lambda c, n=3: [],
        ),
        # hermetic: _build_category_ask must not read the real ranking CSV
        patch.object(ainv_chat, "category_subgroup", new=lambda c: "high_beta_equities"),
        patch.object(ainv_chat, "category_status", new=lambda *a, **k: "in_plan"),
        _patch_probe_format(),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("which smallcap fund?")))

    assert result.text == "probe reply"
    compute.assert_not_awaited()


def test_category_with_amount_computes_and_passes_focus_category():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(output=_output(), run_id=None)
    compute = AsyncMock(return_value=outcome)
    seen_facts = {}

    async def _fake_format(**kwargs):
        seen_facts.update(kwargs.get("facts_pack") or {})
        return "plan + category reply"

    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(500000.0, Cadence.LUMPSUM, "smallcap", None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(ainv_chat, "resolve_category", new=lambda t: "Small Cap Fund"),
        patch.object(ainv_chat, "top_funds_for_category", new=lambda c, n=3: []),
        patch.object(ainv_chat, "category_subgroup", new=lambda c: "high_beta_equities"),
        # hermetic: category_status internally reads the ranking CSV — patch it
        patch.object(ainv_chat, "category_status", new=lambda *a, **k: "in_plan"),
        patch.object(ainv_chat, "format_with_telemetry", new=AsyncMock(side_effect=_fake_format)),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("5L in smallcap")))

    assert result.text == "plan + category reply"
    assert compute.await_args.kwargs["focus_category"] == "Small Cap Fund"
    assert seen_facts["category_ask"]["category"] == "Small Cap Fund"


def test_no_category_paths_unchanged():
    """raw_category None → the pre-existing ask-amount flow, byte-identical."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(
            ainv_chat,
            "extract_deploy_request",
            new=AsyncMock(return_value=(None, Cadence.LUMPSUM, None, None)),
        ),
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat,
            "format_relay_or_canned",
            new=AsyncMock(return_value="how much, and lumpsum or SIP?"),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("invest")))

    assert result.text == "how much, and lumpsum or SIP?"
    compute.assert_not_awaited()


# ── active_preferences: a SAVED preference, not target_bucket, decided the
# split (Task 2, 2026-09-20) ─────────────────────────────────────────────────


def _saved_row(*, categories_set: bool):
    """Preference-view row stand-in: class bars always set, sub-categories only
    when `categories_set` (the case-1/case-2 discriminator)."""
    return SimpleNamespace(
        customer_choices=None,
        asset_class_requested={"equity": 60.0, "debt": 30.0, "others": 10.0},
        resolved_targets={"low_beta_equities": 30.0} if categories_set else None,
    )


def _applied_practical():
    return SimpleNamespace(
        human_override_applied=SimpleNamespace(
            preference_applied=True, shortfall_reason=None
        )
    )


def test_facts_pack_omits_active_preferences_by_default():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    assert "active_preferences" not in build_ainv_facts_pack(_output())


def test_class_only_preference_marks_categories_unset():
    """Case 1: the customer set the class bars only — the reply must say WE
    chose the categories, not attribute the split to target_bucket."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    seen_facts = {}

    async def _fake_format(**kwargs):
        seen_facts.update(kwargs.get("facts_pack") or {})
        return "reply"

    ctx = _ctx(
        user_ctx=SimpleNamespace(
            saved_investment_preference=_saved_row(categories_set=False),
            first_name="Tilly",
        )
    )
    with patch.object(
        ainv_chat, "format_with_telemetry", new=AsyncMock(side_effect=_fake_format)
    ):
        asyncio.run(
            ainv_chat._format_or_fallback_ainv(
                ctx, _output(), practical_result=_applied_practical()
            )
        )

    assert seen_facts["active_preferences"]["categories_set"] is False


def test_subcategory_preference_marks_categories_set():
    """Case 2: resolved_targets present — the customer also pinned categories."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    seen_facts = {}

    async def _fake_format(**kwargs):
        seen_facts.update(kwargs.get("facts_pack") or {})
        return "reply"

    ctx = _ctx(
        user_ctx=SimpleNamespace(
            saved_investment_preference=_saved_row(categories_set=True),
            first_name="Tilly",
        )
    )
    with patch.object(
        ainv_chat, "format_with_telemetry", new=AsyncMock(side_effect=_fake_format)
    ):
        asyncio.run(
            ainv_chat._format_or_fallback_ainv(
                ctx, _output(), practical_result=_applied_practical()
            )
        )

    assert seen_facts["active_preferences"]["categories_set"] is True
