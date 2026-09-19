"""Internal section names must not reach the customer."""

from __future__ import annotations

from pathlib import Path

from app.domains.ai_engine.answer_formatter.formatter import (
    FORMATTER_HOUSE_STYLE,
    assemble_prompt,
)

# All of these are concatenated into one system prompt, so a rename must cover all.
_PROMPT_SOURCES = [
    "app/domains/ai_engine/answer_formatter/formatter.py",
    "app/domains/rebalancing/services/rebal_engine/chat.py",
    "app/domains/asset_allocation/services/aa_engine/chat.py",
    "app/domains/additional_investment/services/ainv_engine/chat.py",
    "app/domains/cashflow/services/goal_planning_engine/chat.py",
    "app/domains/mutual_funds/services/mutual_fund_query_service.py",
    "app/domains/general_chat/services/general_chat_engine.py",
]
_REPO = Path(__file__).resolve().parents[4]


def test_house_style_forbids_naming_internal_sections() -> None:
    assert "Never name an internal section" in FORMATTER_HOUSE_STYLE


def test_house_style_teaches_customer_voice_for_gaps() -> None:
    assert "name the MISSING THING in the customer's own terms" in FORMATTER_HOUSE_STYLE
    assert "I can't see the returns on each individual fund yet" in FORMATTER_HOUSE_STYLE


def test_house_style_forbids_plumbing_voice() -> None:
    assert "in my data" in FORMATTER_HOUSE_STYLE


def test_general_chat_inherits_the_rules_from_the_persona_layer() -> None:
    """It carried no copy of its own; the formatter's house style supplies them."""
    from app.domains.general_chat.services.general_chat_engine import _GENERAL_CHAT_BODY

    prompt = assemble_prompt(
        question="how are markets today?",
        action_mode="narrate",
        module_name="general_chat",
        facts_pack={"research_digest": "Nifty flat."},
        body_prompt=_GENERAL_CHAT_BODY,
        history=[],
        profile={"first_name": "A"},
    )
    assert "Never name an internal section" in prompt["system"]
    assert "name the MISSING THING in the customer's own terms" in prompt["system"]


def test_the_old_token_is_gone_from_every_prompt_source() -> None:
    """Exempt only where formatter.py quotes it as a never-write-this example."""
    offenders: list[str] = []
    for rel in _PROMPT_SOURCES:
        for i, line in enumerate((_REPO / rel).read_text().splitlines(), 1):
            if "FACTS_PACK" not in line:
                continue
            if rel.endswith("formatter.py") and (
                line.lstrip().startswith("#") or "never" in line.lower()
            ):
                continue                      # documented history, not live instruction
            offenders.append(f"{rel}:{i}")
    assert not offenders, f"old token still in prompt text: {offenders}"


def test_assembled_prompt_uses_the_neutral_label() -> None:
    prompt = assemble_prompt(
        question="how am I doing?",
        action_mode="read",
        module_name="portfolio",
        facts_pack={"total_value": 100},
        body_prompt="body",
        history=[],
        profile={"first_name": "A"},
    )
    assert "CUSTOMER_RECORD:" in prompt["user"]
    assert "FACTS_PACK" not in prompt["user"]
