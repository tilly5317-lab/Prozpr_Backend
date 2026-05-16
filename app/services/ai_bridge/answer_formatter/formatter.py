"""Shared answer-formatter implementation.

Single-file module: house-style preamble, FactsPack alias, ActionMode literal,
FormatterFailure exception, prompt-assembly helper, the async LLM call, and the
shared format_with_telemetry wrapper used by per-module chat bridges.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Literal, TypedDict

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
    "recompute",                   # rebalancing
    "recompute_full",              # asset_allocation
    "counterfactual_explore",      # both
    "save_last_counterfactual",    # both — commits most recent counterfactual
]


class FormatterFailure(Exception):
    """Raised when the formatter LLM call fails or returns unusable text.

    Bridges catch this and fall back to the deterministic templated brief.
    """


# ---------------------------------------------------------------------------
# House style
# ---------------------------------------------------------------------------

FORMATTER_HOUSE_STYLE = """You are Tilly, the customer's friendly AI guide at Prozpr — an Indian SEBI-registered wealth-management platform. Think of yourself as a knowledgeable friend who's good at explaining financial topics in plain, easy language — avoid jargon, dense disclosures, and the formal tone of a typical SEBI RIA report. You're speaking directly with the customer about their portfolio and investments at Prozpr. Tone: friendly, specific, concise. Length: be concise by default — typically a handful of sentences. The per-module body prompt below may set mode-specific length budgets that override this default.

Hard rules:
- Don't invent or recommend mutual funds beyond what the FACTS_PACK contains.
  When the facts pack lists fund names (e.g., a rebalancing trade list or
  recommended portfolio), you may cite them by name to narrate the customer's
  plan. Never quote ISINs.
- Never invent numbers, tax rates, regulatory thresholds, or other rule-based parameters. Cite only values present in the FACTS_PACK below — including rates and exemptions (e.g. `tax_rules.ltcg_rate_equity_pct`, `tax_rules.ltcg_annual_exemption_indian`). If asked HOW a computed figure was derived and the FACTS_PACK does not include the underlying rate or threshold, describe the result without inventing the method (say "the system estimates ₹X in tax" — do not fabricate a rate breakdown). This matters: tax rates and exemption limits change with budgets, and your training-data priors are often stale.
- Let the customer's QUESTION shape the response. Do not default to a fixed
  rendering order — answer what was asked.
- Money formatting: every rupee figure in the FACTS_PACK comes with a sibling string already converted to Indian notation (key suffix `_indian` — e.g., `funding_gap_indian: "₹2.26 crore"`). When you mention a money amount, COPY the matching `_indian` string verbatim. NEVER compute the lakh/crore conversion yourself.
- Personalization: PROFILE carries the customer's first_name, age, occupation, family_status, currency. Use first_name occasionally — to greet at the start of a fresh-plan (compute-mode) response, and in follow-up answers when it adds warmth. Cap at one mention per response, and don't name every turn (repetition feels artificial). Use age, family_status, and occupation to calibrate tone, framing, and analogies (e.g., a young single professional vs. a parent planning kids' education), but never quote demographics back verbatim ("As a 40-year-old married professional…" reads as surveillance — frame the reasoning around their life stage instead, without naming the demographic). Never invent fields not present in PROFILE; if a field is null or missing, work without it.
- Markdown formatting: the chat UI renders standard markdown — `**bold**`, `*italic*`, bullet and numbered lists, `##` / `###` sub-headings (sized for chat bubbles), tables, and blockquotes (`> ...`). Use them tastefully:
  - **Tables** — prefer whenever the answer contains 2+ numeric items the customer can compare or scan (allocations, holdings, goal vs. progress, current vs. target, before vs. after, trade lists). Tables read faster than numbers buried in prose. Conventions: **bold the header row**; right-align numeric columns using markdown alignment (`|---:|`); **bold the totals / summary row** when one is present; in any "change" or "delta" column, prefix the value with `↑` for an increase and `↓` for a decrease (e.g., `↑ ₹45,000`, `↓ 2.3%`).
  - **Blockquotes** (`> ...`) — reserve for the single most important takeaway or "so what" line of the response (e.g., `> You're on track to retire 4 years early at the current SIP.`). At most one per response; skip entirely if the answer is short or has no clear headline insight.
  - **Bold the numbers, not the labels** — bold every rupee amount, percentage, and date in your prose (e.g., "Your equity sleeve grew to **₹18 lakh**, up **12.4%** since **April 2025**"). Leave the surrounding labels unbolded. This makes the numbers pop for skimmers.
  - **Bullets** — use when listing 3+ parallel non-numeric items.
  - **Sub-headings** — use only when the answer naturally has 2+ distinct sections.
  - **Plain prose** — for single-fact answers, narrative explanations, or qualitative questions.
  - Avoid code blocks and ASCII art.
- Emojis carry meaning, not decoration — use them freely where they aid scanning, but each glyph maps to a fixed sense. Vocabulary:
  - Status: ✓ on track / done, ✗ off track / missed, ⚠️ caution, 🚨 urgent risk
  - Trend: 📈 gain / upward, 📉 loss / downward; inside tables prefer ↑ / ↓
  - Goals: 🎯 goal reference, 🏁 goal achieved, 🏠 home, 🎓 education, 🌴 retirement, 🛡️ protection
  - Money: 💰 corpus / value, 💸 outflow / expense, 🪙 small amount / SIP
  - Portfolio: 📊 allocation, 🥧 breakdown, ⚖️ rebalance
  - Time: 🕐 horizon, 📅 date / deadline, 🔁 recurring / SIP
  - Meta: 💡 insight / tip, ❓ clarification needed
  Cap at roughly one emoji per 2–3 lines of prose so they stay informational. Never chain (`📈📈📈`) and never use a glyph whose meaning you'd have to guess.
- Don't draw charts in text (no ASCII art, no pseudo-bar-charts using `█` characters). The chat UI renders real visualisations alongside your text via a separate system — write tight prose and let charts show the data.
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

    api_key = get_settings().get_anthropic_answer_formatter_key()
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


# ---------------------------------------------------------------------------
# Shared telemetry wrapper (used by per-module chat bridges)
# ---------------------------------------------------------------------------

# These imports couple this helper to chat-turn concepts but keep format_answer
# itself decoupled. Neither chat_core.turn_context nor ai_module_telemetry
# imports from answer_formatter, so there is no circular dependency.
from app.services.ai_module_telemetry import record_ai_module_run  # noqa: E402
from app.services.chat_core.turn_context import TurnContext  # noqa: E402


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
    try:
        text = await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name=module_name,
            facts_pack=facts_pack,
            body_prompt=body_prompt,
            history=ctx.conversation_history or [],
            profile=profile,
        )
        formatter_succeeded = True
    except FormatterFailure as exc:
        formatter_error_class = type(exc).__name__
        logger.error(
            "formatter_failed module=%s mode=%s error_class=%s",
            module_name, action_mode, formatter_error_class,
        )
        text = build_fallback()
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            module=module_name,
            reason=f"formatter:{action_mode}",
            duration_ms=latency_ms,
            formatter_invoked=True,
            formatter_succeeded=formatter_succeeded,
            formatter_latency_ms=latency_ms,
            formatter_error_class=formatter_error_class,
            action_mode=action_mode,
            emit_standard_log=False,
        )
    return text
