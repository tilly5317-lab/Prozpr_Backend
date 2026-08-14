"""Shared answer-formatter implementation.

Single-file module: house-style preamble, FactsPack alias, ActionMode literal,
FormatterFailure exception, prompt-assembly helper, the async LLM call, and the
shared format_with_telemetry wrapper used by per-module chat bridges.
"""

from __future__ import annotations

import json
import logging
import time
from functools import partial
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

# Modes that pass through the formatter. A module's own "clarify" is NOT a mode
# here — a module that asks the customer for an input routes that through the
# formatter as `gather` (rebalancing does; cashflow still returns its clarify
# text raw). Prefer `gather`: returning the detector's text directly skips PI's
# voice AND writes no telemetry row, and it was that missing row that let
# rebalancing ask the same question four turns running with nothing able to
# notice (`rebalancing/services/rebal_engine/chat.py`, `_last_action_mode`).
ActionMode = Literal[
    "compute",  # a freshly produced result — first run or an explicit re-run
    "narrate",
    "educate",
    "counterfactual_explore",
    "consolidate",  # rebalancing
    "screen",  # mutual_fund_query — rank our universe
    "fund_detail",  # mutual_fund_query — one named fund (or a head-to-head)
    "category_probe",  # additional_investment
    "gather",  # we can do this; one input is missing
    "redirect",  # we don't do this
]


class FormatterFailure(Exception):
    """Raised when the formatter LLM call fails or returns unusable text.

    Bridges catch this and fall back to the deterministic templated brief.
    """


# ---------------------------------------------------------------------------
# House style
# ---------------------------------------------------------------------------

_FORMATTER_FACTS_NOTES = (
    "Working from CUSTOMER_RECORD:\n"
    "- Cite only values present in CUSTOMER_RECORD (including rates/exemptions such as "
    "`tax_rules.ltcg_rate_equity_pct`, `tax_rules.ltcg_annual_exemption_indian`). If asked HOW a "
    "computed figure was derived and CUSTOMER_RECORD lacks the underlying rate or threshold, "
    'describe the result without inventing the method (e.g. "the system estimates ₹X in tax").\n'
    "- A LOGIC_REFERENCE section may follow the flow instructions: Prozpr's published methodology "
    "(philosophy only). Ground 'how/why does the approach work' answers in it and nothing else; "
    "keep every figure sourced from CUSTOMER_RECORD. If asked for a threshold, cap or weight that "
    "appears in neither, say it's a policy parameter we don't publish — never estimate one.\n"
    "- The whole-number asset-class rule applies to every mix field in CUSTOMER_RECORD: "
    "`plan_target_pct`, `planned_split_pct`, `asset_class_mix_pct`, "
    "`current_asset_class_mix_pct`, `target_asset_class_mix_pct`, `by_horizon[*].mix_pct`, and "
    "`your_actual_holdings_today_pct` (show a separate **Cash** label when a `cash` key is present).\n"
    "- On a fresh-plan (compute-mode) reply you may greet with the customer's first_name; in "
    "follow-ups use it only when it adds warmth."
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
    # Compact + literal ₹: pretty separators and \uXXXX escapes inflated a
    # 20-holding portfolio pack by 11% at zero information gain, and the money
    # rule tells the model to copy `_indian` strings verbatim — easier from a
    # literal ₹ than from an escape.
    _dump = partial(json.dumps, separators=(",", ":"), ensure_ascii=False, default=str)
    user = (
        f"MODULE: {module_name}\n"
        f"ACTION_MODE: {action_mode}\n\n"
        f"CUSTOMER_RECORD:\n{_dump(facts_pack)}\n\n"
        f"PROFILE:\n{_dump(profile)}\n\n"
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
    extra_tool_fields: dict[str, Any] | None = None,
    extras_out: dict[str, Any] | None = None,
    allow_empty_answer: bool = False,
) -> str:
    """Async Haiku call. Raises FormatterFailure on any failure mode.

    Caller is expected to wrap in try/except and fall back to a templated brief.

    ``extra_tool_fields`` adds non-prose properties (booleans, enums, short control
    strings) to the forced tool; their values land in ``extras_out``. Set
    ``allow_empty_answer`` when a null ``answer`` is a meaningful outcome for the
    caller rather than a failure — it then returns ``""``.
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
        text = await _invoke_llm(
            prompt["system"],
            prompt["user"],
            module_name,
            extra_tool_fields=extra_tool_fields,
            extras_out=extras_out,
            allow_empty_answer=allow_empty_answer,
        )
    except FormatterFailure:
        raise
    except Exception as exc:
        raise FormatterFailure(
            f"formatter_llm_call_failed: {type(exc).__name__}"
        ) from exc

    if not text or not text.strip():
        if allow_empty_answer:
            return ""
        raise FormatterFailure("formatter_llm_returned_empty")
    return text


def _read_extras(response, wanted: dict[str, Any]) -> dict[str, Any]:
    """Pull the non-answer tool fields out of a forced-tool response."""
    for call in getattr(response, "tool_calls", None) or []:
        args = (call.get("args") if isinstance(call, dict) else None) or {}
        return {k: args.get(k) for k in wanted}
    return {}


async def _invoke_llm(
    system_text: str,
    user_text: str,
    module_name: str,
    extra_tool_fields: dict[str, Any] | None = None,
    extras_out: dict[str, Any] | None = None,
    allow_empty_answer: bool = False,
) -> str:
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
    from app.domains.ai_engine.streaming import (
        FINE_GRAINED_TOOL_STREAMING,
        astream_tool_answer,
        current_token_stream,
    )

    # Attributed to the module whose reply this is, so per-module cost tracking
    # covers formatter spend too. Falls back to the shared formatter key, then
    # the global one.
    api_key = get_settings().get_anthropic_formatter_key_for(module_name)
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
                    # Nullable only for callers that route an out-of-scope turn through a
                    # separate field — a null answer is then the signal, not a failure.
                    "type": ["string", "null"] if allow_empty_answer else "string",
                    "description": (
                        "The clean, customer-facing answer in PI's voice and the required "
                        "markdown format. No preamble, no internal field names or raw N/10 scores."
                    ),
                },
                # Non-prose metadata only. A second PROSE field competes with `answer`
                # for the same content — that is what broke the old reasoning-first
                # scratchpad (see the note above). Booleans, enums and short control
                # strings do not.
                **(extra_tool_fields or {}),
            },
            "required": ["answer"],
        },
    }
    _llm_kwargs = {
        "model": "claude-haiku-4-5-20251001",
        "api_key": api_key,
        "max_tokens": 6000,  # room for long allocation / rebalancing answers
    }
    # Pinned. Left unset, the API default of 1.0 applied and the same question
    # returned different figures run to run — measured: "when do I reach ₹10
    # crore?" answered FY2034/₹10.44cr and FY2035/₹11.85cr on consecutive calls.
    # The env var stays as an escape hatch, but 0 is the default now.
    _llm_kwargs["temperature"] = float(
        os.environ.get("AILAX_FORMATTER_TEMPERATURE", "0")
    )
    # Without this beta the API withholds the tool's input JSON until the end,
    # so streaming would show nothing for most of the call. Set only while
    # streaming — it relaxes server-side JSON validation, which buys nothing on
    # the blocking path.
    streaming = current_token_stream() is not None
    if streaming:
        _llm_kwargs["betas"] = [FINE_GRAINED_TOOL_STREAMING]
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
    # Streams only when a turn has an open token stream; otherwise this is the
    # exact ainvoke path it has always been.
    if streaming:
        raw = await astream_tool_answer(llm, messages)
    else:
        raw = await llm.ainvoke(messages)
    stop_reason = getattr(raw, "response_metadata", {}).get("stop_reason")
    if stop_reason == "max_tokens":
        # Mid-response truncation looks worse than the deterministic fallback brief.
        raise FormatterFailure("formatter_truncated_at_max_tokens")
    if extras_out is not None and extra_tool_fields:
        extras_out.update(_read_extras(raw, extra_tool_fields))
    answer = extract_reasoned_reply(raw)
    if not answer:
        if allow_empty_answer:
            return ""
        raise FormatterFailure("formatter_no_tool_call")
    return answer


# ---------------------------------------------------------------------------
# Shared telemetry wrapper (used by per-module chat bridges)
# ---------------------------------------------------------------------------

# These imports couple this helper to chat-turn concepts but keep format_answer
# itself decoupled. Neither chat_core.turn_context nor ai_module_telemetry
# imports from answer_formatter, so there is no circular dependency.
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run  # noqa: E402
from app.domains.ai_engine.logic_docs import logic_reference_for  # noqa: E402
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
    action_mode: ActionMode,
    profile: dict[str, Any],
    build_fallback: Callable[[], str],
    extra_tool_fields: dict[str, Any] | None = None,
    extras_out: dict[str, Any] | None = None,
    allow_empty_answer: bool = False,
    history_override: list[dict[str, Any]] | None = None,
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
    # Each module's explain-the-why mode(s) carry its client-safe Logics thesis
    # doc so "how/why does the approach work" answers ground in published
    # methodology instead of the LLM's general knowledge. Which modes those are
    # is per module (see logic_docs._MODULE_DOC_MODES).
    logic_reference = logic_reference_for(module_name, action_mode)
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
                # A module may pre-process history (e.g. portfolio annotates time
                # gaps so a fortnight-old thread isn't read as live context).
                history=(
                    history_override
                    if history_override is not None
                    else (ctx.conversation_history or [])
                ),
                profile=profile,
                logic_reference=logic_reference,
                extra_tool_fields=extra_tool_fields,
                extras_out=extras_out,
                allow_empty_answer=allow_empty_answer,
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
    "You are relaying a LIMIT to the customer: something Prozpr does not do, or "
    "cannot do from chat.\n"
    "\n"
    "CUSTOMER_RECORD has a single field, `boundary_message`: the exact limit to "
    "convey.\n"
    "\n"
    "Convey `boundary_message` faithfully in PI's voice, leading with it as the "
    "house rules describe — acknowledge what was asked only when the limit would "
    "otherwise read as a non-sequitur. Do NOT perform or simulate the requested "
    "action, and do NOT add capabilities, steps, or requirements beyond "
    "`boundary_message`. Keep it to 2-4 sentences, warm."
)

_GATHER_BODY = (
    "You are asking the customer for ONE missing input so you can do what they "
    "asked. This is not a refusal and not a limit — we can do this, we just need "
    "the input.\n"
    "\n"
    "CUSTOMER_RECORD has a single field, `boundary_message`: what is missing.\n"
    "\n"
    "- Lead with the question. Do not open by restating their request, and never "
    "frame it as something you cannot do — no \"but\", no \"before I can\", no "
    "apology. \"How much would you like to invest?\" is a complete reply.\n"
    "- Ask ONLY for what `boundary_message` names. Do not add requirements it "
    "does not mention.\n"
    "- If RECENT_HISTORY shows you already asked this and the customer repeated "
    "themselves, do not repeat the same wording — they did not understand it or "
    "did not think it applied. Ask more concretely, with an example value.\n"
    "- One or two sentences. Warm, direct, no preamble."
)


async def format_relay_or_canned(
    *,
    ctx: TurnContext,
    module_name: str,
    message: str,
    action_mode: ActionMode = "redirect",
) -> str:
    """Tailor a short canned ``message`` via the formatter; fall back to it
    verbatim on failure.

    Pass ``action_mode="gather"`` when the message asks the customer for an input
    we need — it selects a body prompt that leads with the question instead of a
    limit. The default relays a genuine boundary.
    """
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"boundary_message": message},
        body_prompt=_GATHER_BODY if action_mode == "gather" else _RELAY_BODY,
        module_name=module_name,
        action_mode=action_mode,
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: message,
    )
