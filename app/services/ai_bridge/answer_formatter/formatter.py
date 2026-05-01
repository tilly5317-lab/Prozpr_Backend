"""Shared answer-formatter implementation.

Single-file module: house-style preamble, FactsPack alias, ActionMode literal,
FormatterFailure exception, prompt-assembly helper, and the async LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Module-supplied dict — flat-ish, JSON-serializable, ≤ ~1500 tokens.
FactsPack = dict[str, Any]

# Modes that pass through the formatter. clarify / redirect bypass it.
ActionMode = Literal[
    "compute",
    "narrate",
    "educate",
    "recompute_full",
    "recompute_with_overrides",
    "counterfactual_explore",
]


class FormatterFailure(Exception):
    """Raised when the formatter LLM call fails or returns unusable text.

    Bridges catch this and fall back to the deterministic templated brief.
    """


# ---------------------------------------------------------------------------
# House style
# ---------------------------------------------------------------------------

FORMATTER_HOUSE_STYLE = """You are Prozpr, an Indian financial advisor speaking
to a customer about their goal-based investment plan. Tone: warm, specific,
concise. Length: 4-8 sentences unless the question demands more.

Hard rules:
- Never recommend a specific mutual fund, ISIN, or scheme name.
- Never invent numbers. Cite only values present in the FACTS_PACK below.
- Let the customer's QUESTION shape the response. Do not default to a fixed
  rendering order — answer what was asked.
- Use ₹ for amounts; render as "₹X,XX,XXX" (Indian numbering) or "₹X lakh" /
  "₹X crore" where natural.
- When the question can't be answered from the FACTS_PACK, say so plainly and
  offer a next step.

This is general information, not personalized advice. Do not promise outcomes.
"""


class _Prompt(TypedDict):
    system: str
    user: str


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------

def assemble_prompt(
    *,
    question: str,
    action_mode: str,
    module_name: str,
    facts_pack: FactsPack,
    body_prompt: str,
    history: list[dict[str, Any]],
    profile: dict[str, Any],
) -> _Prompt:
    """Build the (system, user) prompt pair. Pure — no LLM call."""
    system = "\n\n".join([FORMATTER_HOUSE_STYLE, body_prompt])
    history_lines = [
        f"{m.get('role','user')}: {m.get('content','')}"
        for m in (history or [])[-6:]
    ]
    user = (
        f"MODULE: {module_name}\n"
        f"ACTION_MODE: {action_mode}\n\n"
        f"FACTS_PACK:\n{json.dumps(facts_pack, default=str)}\n\n"
        f"PROFILE:\n{json.dumps(profile, default=str)}\n\n"
        f"RECENT_HISTORY:\n" + "\n".join(history_lines) + "\n\n"
        f"CUSTOMER_QUESTION: {question}"
    )
    return {"system": system, "user": user}


# ---------------------------------------------------------------------------
# Async LLM call
# ---------------------------------------------------------------------------

async def format_answer(
    *,
    question: str,
    action_mode: str,
    module_name: str,
    facts_pack: FactsPack,
    body_prompt: str,
    history: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    """Async Haiku call. Raises FormatterFailure on any failure mode.

    Caller is expected to wrap in try/except and fall back to a templated brief.
    """
    prompt = assemble_prompt(
        question=question, action_mode=action_mode, module_name=module_name,
        facts_pack=facts_pack, body_prompt=body_prompt,
        history=history, profile=profile,
    )
    try:
        text = await _invoke_llm(prompt["system"], prompt["user"])
    except Exception as exc:
        raise FormatterFailure(f"formatter_llm_call_failed: {type(exc).__name__}") from exc

    if not text or not text.strip():
        raise FormatterFailure("formatter_llm_returned_empty")
    return text


async def _invoke_llm(system_text: str, user_text: str) -> str:
    """Single Haiku 4.5 call; isolated so tests can patch it."""
    # Imported lazily to keep test stubs cheap.
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.config import get_settings

    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=600,
    )
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return getattr(raw, "content", "") or ""
