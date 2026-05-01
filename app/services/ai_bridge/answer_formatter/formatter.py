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
# Async LLM call (filled in in Task 3)
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
    """Stub — Task 3 wires the LangChain Anthropic call."""
    raise NotImplementedError("format_answer is wired in Task 3")
