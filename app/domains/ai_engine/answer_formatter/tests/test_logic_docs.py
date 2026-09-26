"""Tests for the Logics-doc grounding: loader (logic_docs.py) + formatter injection.

The loader maps module_name -> client-safe thesis doc(s); format_with_telemetry
attaches them as LOGIC_REFERENCE only on each module's own explain-the-why
mode(s) (per logic_docs._MODULE_DOC_MODES), so mechanism answers ground in
published methodology.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domains.ai_engine.logic_docs import (
    _DOCS_DIR,
    _MODULE_DOCS,
    _MODULE_DOC_MODES,
    get_logic_reference,
    logic_reference_for,
)
from app.domains.ai_engine.answer_formatter import formatter as fmt_mod
from app.domains.ai_engine.answer_formatter import assemble_prompt


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_all_mapped_docs_exist_on_disk():
    """Drift guard: every file the mapping names must exist in the repo."""
    for filenames in _MODULE_DOCS.values():
        for filename in filenames:
            assert (_DOCS_DIR / filename).is_file(), f"missing logic doc: {filename}"


def test_rebalancing_reference_concatenates_both_theses():
    text = get_logic_reference("rebalancing")
    assert text is not None
    assert "Portfolio Rebalancing Thesis" in text
    assert "Practical Asset Allocation Thesis" in text


def test_unknown_module_returns_none():
    assert get_logic_reference("portfolio_query") is None
    assert get_logic_reference("") is None


def test_doc_modes_gate_per_module():
    # Explain-the-why modes attach the doc where each module keeps that answer.
    assert logic_reference_for("rebalancing", "narrate") is not None
    assert logic_reference_for("rebalancing", "educate") is not None
    assert logic_reference_for("goal_planning", "narrate") is not None
    # The two docs that used to be unreachable now attach on the module's own
    # explanatory mode (mutual_fund_query has no narrate/educate; so did ainv).
    assert logic_reference_for("mutual_fund_query", "fund_detail") is not None
    assert logic_reference_for("additional_investment", "category_probe") is not None
    # Lean modes and modules with no doc stay off.
    assert logic_reference_for("rebalancing", "compute") is None
    assert logic_reference_for("mutual_fund_query", "screen") is None
    assert logic_reference_for("additional_investment", "compute") is None
    assert logic_reference_for("portfolio_query", "narrate") is None


def test_no_dead_doc_config():
    """Every module that maps a doc must list at least one mode to attach it —
    the bug this wiring fixed (mutual_fund_query / additional_investment docs
    were mapped but unreachable)."""
    for module in _MODULE_DOCS:
        assert _MODULE_DOC_MODES.get(module), f"{module} doc unreachable (no mode)"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_assemble_prompt_appends_labelled_logic_reference_to_system():
    prompt = assemble_prompt(
        question="why do you sell long-term units first?",
        action_mode="educate",
        module_name="rebalancing",
        facts_pack={},
        body_prompt="MODULE-BODY",
        history=[],
        profile={},
        logic_reference="THESIS-TEXT",
    )
    assert "LOGIC_REFERENCE — Prozpr's published methodology" in prompt["system"]
    assert "THESIS-TEXT" in prompt["system"]
    # Doc rides in the (cacheable) system prompt, not the user message.
    assert "THESIS-TEXT" not in prompt["user"]


def test_assemble_prompt_omits_block_when_no_logic_reference():
    prompt = assemble_prompt(
        question="?", action_mode="compute", module_name="rebalancing",
        facts_pack={}, body_prompt="b", history=[], profile={},
    )
    # The house-style guardrail mentions LOGIC_REFERENCE by name; what must be
    # absent is the injected block itself.
    assert "LOGIC_REFERENCE — Prozpr's published methodology" not in prompt["system"]


def test_house_style_carries_the_no_unpublished_numbers_guardrail():
    text = fmt_mod.FORMATTER_HOUSE_STYLE
    assert "LOGIC_REFERENCE" in text
    assert "never estimate one" in text


# ---------------------------------------------------------------------------
# Injection via format_with_telemetry (mode-gated)
# ---------------------------------------------------------------------------


def _run_format_with_telemetry(action_mode: str) -> dict:
    captured: dict = {}

    async def fake_format_answer(**kwargs):
        captured.update(kwargs)
        return "ANSWER"

    ctx = SimpleNamespace(
        user_question="how does this work?",
        conversation_history=[],
        db=None,
        effective_user_id=1,
        session_id=None,
    )
    with (
        patch.object(fmt_mod, "format_answer", new=fake_format_answer),
        patch.object(fmt_mod, "record_ai_module_run", new=AsyncMock()),
    ):
        asyncio.run(fmt_mod.format_with_telemetry(
            ctx=ctx,
            facts_pack={},
            body_prompt="b",
            module_name="rebalancing",
            action_mode=action_mode,
            profile={},
            build_fallback=lambda: "FALLBACK",
        ))
    return captured


def test_educate_mode_injects_the_module_thesis():
    captured = _run_format_with_telemetry("educate")
    assert captured["logic_reference"] is not None
    assert "Portfolio Rebalancing Thesis" in captured["logic_reference"]


def test_compute_mode_stays_lean():
    captured = _run_format_with_telemetry("compute")
    assert captured["logic_reference"] is None
