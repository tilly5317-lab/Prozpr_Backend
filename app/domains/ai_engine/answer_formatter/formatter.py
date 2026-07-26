"""Shared answer-formatter implementation.

Single-file module: house-style preamble, FactsPack alias, ActionMode literal,
FormatterFailure exception, prompt-assembly helper, the async LLM call, and the
shared format_with_telemetry wrapper used by per-module chat bridges.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Literal, TypedDict

# Shared persona builder (AI_Agents/src/persona.py, stdlib-only). Imported via an
# explicit sys.path injection rather than the app re-export to avoid an import
# cycle through the app.domains.ai_engine package __init__.
import sys
from pathlib import Path as _Path

_AI_AGENTS_SRC = str(_Path(__file__).resolve().parents[4] / "AI_Agents" / "src")
if _AI_AGENTS_SRC not in sys.path:
    sys.path.insert(0, _AI_AGENTS_SRC)
from persona import build_system_prompt  # noqa: E402
from reasoned_reply import extract_reasoned_reply  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Module-supplied dict — flat-ish, JSON-serializable, ≤ ~1500 tokens.
FactsPack = dict[str, Any]

# Modes that pass through the formatter. clarify bypasses it.
ActionMode = Literal[
    "compute",
    "narrate",
    "educate",
    "recompute",  # rebalancing
    "recompute_full",  # asset_allocation
    "counterfactual_explore",  # both
    "redirect",  # out_of_scope / stock_advice
]


class FormatterFailure(Exception):
    """Raised when the formatter LLM call fails or returns unusable text.

    Bridges catch this and fall back to the deterministic templated brief.
    """


# ---------------------------------------------------------------------------
# House style
# ---------------------------------------------------------------------------

_FORMATTER_FACTS_NOTES = (
    "Working from the FACTS_PACK:\n"
    "- Cite only values present in the FACTS_PACK (including rates/exemptions such as "
    "`tax_rules.ltcg_rate_equity_pct`, `tax_rules.ltcg_annual_exemption_indian`). If asked HOW a "
    "computed figure was derived and the FACTS_PACK lacks the underlying rate or threshold, "
    'describe the result without inventing the method (e.g. "the system estimates ₹X in tax").\n'
    "- A LOGIC_REFERENCE section may follow the flow instructions: Prozpr's published methodology "
    "(philosophy only). Ground 'how/why does the approach work' answers in it and nothing else; "
    "keep every figure sourced from the FACTS_PACK. If asked for a threshold, cap or weight that "
    "appears in neither, say it's a policy parameter we don't publish — never estimate one.\n"
    "- The whole-number asset-class rule applies to every mix field in the FACTS_PACK: "
    "`plan_target_pct`, `planned_split_pct`, `asset_class_mix_pct`, `by_horizon[*].mix_pct`, and "
    "`your_actual_holdings_today_pct` (show a separate **Cash** label when a `cash` key is present).\n"
    "- On a fresh-plan (compute-mode) reply you may greet with the customer's first_name; in "
    "follow-ups use it only when it adds warmth.\n"
    "- When the question can't be answered from the FACTS_PACK, say so plainly and offer a next step."
)

# Backward-compatible alias: the shared chat-profile system prompt (identity, money,
# jargon, markdown/emoji, risk-naming, question-awareness) + the formatter facts notes.
# assemble_prompt() still appends each module's body_prompt after this.
FORMATTER_HOUSE_STYLE = build_system_prompt(
    _FORMATTER_FACTS_NOTES, format_profile="chat", question_aware=True
)


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
    logic_reference: str | None = None,
) -> _Prompt:
    """Build the (system, user) prompt pair. Pure — no LLM call."""
    system_parts = [FORMATTER_HOUSE_STYLE, body_prompt]
    if logic_reference:
        system_parts.append(
            "LOGIC_REFERENCE — Prozpr's published methodology for this module "
            "(philosophy only; it deliberately contains no proprietary thresholds, "
            "caps or weights):\n\n" + logic_reference
        )
    system = "\n\n".join(system_parts)
    history_lines = [
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in (history or [])[-6:]
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
    logic_reference: str | None = None,
) -> str:
    """Async Haiku call. Raises FormatterFailure on any failure mode.

    Caller is expected to wrap in try/except and fall back to a templated brief.
    """
    prompt = assemble_prompt(
        question=question,
        action_mode=action_mode,
        module_name=module_name,
        facts_pack=facts_pack,
        body_prompt=body_prompt,
        history=history,
        profile=profile,
        logic_reference=logic_reference,
    )
    try:
        text = await _invoke_llm(prompt["system"], prompt["user"])
    except FormatterFailure:
        raise
    except Exception as exc:
        raise FormatterFailure(
            f"formatter_llm_call_failed: {type(exc).__name__}"
        ) from exc

    if not text or not text.strip():
        raise FormatterFailure("formatter_llm_returned_empty")
    return text


async def _invoke_llm(system_text: str, user_text: str) -> str:
    """Single Haiku 4.5 call via a forced answer-only tool.

    A discarded reasoning-first scratchpad was tried here, but on long
    allocation/rebalancing answers the model dumped its analysis into ``reasoning``
    and ~half the time omitted the ``answer`` field entirely (a reasoning-only tool
    call) → fallback to the raw brief. An answer-only forced tool is reliable: there
    is no competing field to dump into. Reasoning separation still applies on the
    short market-commentary QA / doc-gen surfaces. Isolated so tests can patch it
    (and ChatAnthropic beneath it)."""
    # Imported lazily to keep test stubs cheap.
    import os
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.core.config import get_settings

    api_key = get_settings().get_anthropic_answer_formatter_key()
    tool = {
        "name": "return_formatted_answer",
        "description": (
            "Return the final customer-facing answer. Call this exactly once and put the "
            "complete reply in `answer`; emit no text outside this call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": (
                        "The clean, customer-facing answer in PI's voice and the required "
                        "markdown format. No preamble, no internal field names or raw N/10 scores."
                    ),
                },
            },
            "required": ["answer"],
        },
    }
    _llm_kwargs = {
        "model": "claude-haiku-4-5-20251001",
        "api_key": api_key,
        "max_tokens": 6000,  # room for long allocation / rebalancing answers
    }
    # Eval mode sets this to 0 for reproducible output; prod leaves it unset (default temp).
    _temp = os.environ.get("AILAX_FORMATTER_TEMPERATURE")
    if _temp is not None:
        _llm_kwargs["temperature"] = float(_temp)
    llm = ChatAnthropic(**_llm_kwargs).bind_tools(
        [tool], tool_choice={"type": "tool", "name": "return_formatted_answer"}
    )
    messages = [
        SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        ),
        HumanMessage(content=user_text),
    ]
    # Native async (not to_thread): lets the flow timeout actually cancel the
    # HTTP call — a cancelled thread would keep running to completion.
    raw = await llm.ainvoke(messages)
    stop_reason = getattr(raw, "response_metadata", {}).get("stop_reason")
    if stop_reason == "max_tokens":
        # Mid-response truncation looks worse than the deterministic fallback brief.
        raise FormatterFailure("formatter_truncated_at_max_tokens")
    answer = extract_reasoned_reply(raw)
    if not answer:
        raise FormatterFailure("formatter_no_tool_call")
    return answer


# ---------------------------------------------------------------------------
# Shared telemetry wrapper (used by per-module chat bridges)
# ---------------------------------------------------------------------------

# These imports couple this helper to chat-turn concepts but keep format_answer
# itself decoupled. Neither chat_core.turn_context nor ai_module_telemetry
# imports from answer_formatter, so there is no circular dependency.
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run  # noqa: E402
from app.domains.ai_engine.logic_docs import LOGIC_DOC_MODES, get_logic_reference  # noqa: E402
from app.domains.ai_engine.turn_context import TurnContext  # noqa: E402
from app.domains.ai_engine.usage_tracking import (  # noqa: E402
    jsonable_llm_usage,
    track_formatter_llm_usage,
)


async def format_with_telemetry(
    *,
    ctx: TurnContext,
    facts_pack: FactsPack,
    body_prompt: str,
    module_name: str,
    action_mode: str,
    profile: dict[str, Any],
    build_fallback: Callable[[], str],
) -> str:
    """Run the formatter with timing + telemetry; fall back on failure.

    Records a ``ChatAiModuleRun`` row with formatter_invoked / formatter_succeeded /
    formatter_latency_ms / formatter_error_class / action_mode populated.
    On ``FormatterFailure``, calls ``build_fallback()`` (the per-module fallback
    closure) and surfaces its return value as the response text.

    Per-module wrappers in each bridge supply: facts_pack, body_prompt, module
    name, profile, and the fallback closure. They keep their existing signatures
    (asset_allocation passes typed output + spine_mode; rebalancing passes a
    precomputed fallback string from the engine outcome).
    """
    started = time.monotonic()
    formatter_succeeded = False
    formatter_error_class: str | None = None
    # Methodology-shaped modes (educate/narrate) carry the module's client-safe
    # Logics thesis doc so "how/why does the approach work" answers ground in
    # published methodology instead of the LLM's general knowledge.
    logic_reference = (
        get_logic_reference(module_name) if action_mode in LOGIC_DOC_MODES else None
    )
    # Formatter-scoped usage tracker; nests inside the brain's turn-level
    # tracker (both handlers see the call — the turn row still gets the total).
    with track_formatter_llm_usage() as usage_cb:
        try:
            text = await format_answer(
                question=ctx.user_question,
                action_mode=action_mode,
                module_name=module_name,
                facts_pack=facts_pack,
                body_prompt=body_prompt,
                history=ctx.conversation_history or [],
                profile=profile,
                logic_reference=logic_reference,
            )
            formatter_succeeded = True
        except FormatterFailure as exc:
            formatter_error_class = type(exc).__name__
            logger.error(
                "formatter_failed module=%s mode=%s error_class=%s reason=%s",
                module_name,
                action_mode,
                formatter_error_class,
                exc,
            )
            text = build_fallback()
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            llm_usage = jsonable_llm_usage(usage_cb.usage_metadata)
            await record_ai_module_run(
                ctx.db,
                user_id=ctx.effective_user_id,
                session_id=ctx.session_id,
                module=module_name,
                reason=f"formatter:{action_mode}",
                duration_ms=latency_ms,
                extra={"llm_usage": llm_usage} if llm_usage else None,
                formatter_invoked=True,
                formatter_succeeded=formatter_succeeded,
                formatter_latency_ms=latency_ms,
                formatter_error_class=formatter_error_class,
                action_mode=action_mode,
                emit_standard_log=False,
            )
    return text


_RELAY_BODY = (
    "You are relaying a short boundary or next-step message to the customer — "
    "either a limit on what you can do from chat, or something they need to "
    "provide before you can proceed.\n"
    "\n"
    "FACTS_PACK has a single field, `boundary_message`: the exact limit or "
    "instruction to convey.\n"
    "\n"
    "Convey `boundary_message` faithfully in PI's voice, leading with it as the "
    "house rules describe — acknowledge what was asked only when the limit would "
    "otherwise read as a non-sequitur. Do NOT perform or simulate the requested "
    "action, and do NOT add capabilities, steps, or requirements beyond "
    "`boundary_message`. Keep it to 2-4 sentences, warm."
)


async def format_relay_or_canned(
    *,
    ctx: TurnContext,
    module_name: str,
    message: str,
    action_mode: str = "redirect",
) -> str:
    """Tailor a short canned boundary/redirect/gate ``message`` via the formatter;
    fall back to it verbatim on failure. Shared by the in-scope module redirects
    and data-gap gates (mirrors the out_of_scope/stock_advice redirect handler)."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"boundary_message": message},
        body_prompt=_RELAY_BODY,
        module_name=module_name,
        action_mode=action_mode,
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: message,
    )
