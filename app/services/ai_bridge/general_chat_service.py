"""Handle general/market chat queries via Anthropic (Claude Haiku).

Two-pass flow:
  1. Research pass — Haiku with `web_search` allowed. Returns a plain-text
     factual digest citing commentary and/or web results.
  2. Compose pass — Haiku forced to call `return_reply` with strict schema.
     Output is rendered to markdown in Python. No preambles, no XML/citation
     leakage reach the user.
"""

from __future__ import annotations

import json
import re

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.services.ai_bridge.common import (
    build_history_block,
    ensure_ai_agents_path,
    format_inr_indian,
)

ensure_ai_agents_path()

from intent_classifier.models import Intent
from intent_classifier.prompts import OUT_OF_SCOPE_MESSAGE
from intent_classifier import ClassificationResult

# Keep market commentary under this limit so the prompt fits the context window.
_MAX_COMMENTARY_CHARS = 7000

_SYSTEM_PROMPT = (
    "You are Tilly, an Indian-market financial assistant at Prozpr, a SEBI-registered wealth-management platform for retail clients in India. "
    "Think of yourself as a knowledgeable friend who's good at explaining financial topics in plain, easy language — avoid jargon, dense disclosures, and the formal tone of a typical SEBI RIA report. Tone: friendly, specific, concise.\n"
    "\n"
    "Hard rules:\n"
    "- Never recommend a specific mutual fund, ISIN, or scheme name.\n"
    "- Never invent numbers. Cite only values present in `client_context` or the `Market commentary context` in your message.\n"
    "- Money: every rupee field in `client_context` has a sibling `_indian` string "
    "already formatted in Indian notation (e.g., `annual_income_inr: 4500000` is "
    "paired with `annual_income_indian: '₹45 lakh'`) — COPY the matching `_indian` "
    "string verbatim. Figures from the market commentary are pre-formatted by "
    "upstream — copy those verbatim too. NEVER convert raw rupees to lakh/crore "
    "yourself. NEVER say 'million' or 'billion'.\n"
    "- Don't draw charts in text (no ASCII art, no pseudo-bar-charts using `█` characters). The chat UI renders real visualisations alongside your text — write tight prose and let charts show the data.\n"
    "- This is general information, not personalized advice. Do not promise outcomes.\n"
    "\n"
    "Data source priority (strict — follow in order):\n"
    "1. FIRST, try to answer using the 'Market commentary context' section of the "
    "user message. This is our daily-refreshed Indian macro snapshot and is the "
    "preferred source. If any relevant figure is present in that section, you MUST "
    "answer using only that source and MUST NOT call the `web_search` tool. Do not "
    "cross-validate, double-check, or 'confirm' commentary figures with a web "
    "search — cite the commentary value and stop.\n"
    "2. Call `web_search` ONLY when the requested figure is entirely absent from the "
    "market commentary. In that case, frame the query to be India-specific (e.g., "
    "'Nifty 50 PE ratio today', 'RBI repo rate latest', 'USD INR spot rate').\n"
    "3. Do NOT cite, estimate, or recall market data from your training knowledge. "
    "Training data is stale and is never an acceptable source for numeric market "
    "figures. Use the market commentary or web_search — nothing else.\n"
    "\n"
    "Geographic default: India (Nifty 50, Sensex, RBI, 10-yr G-Sec, INR). Treat "
    "any unqualified market question as an Indian-market question unless the "
    "user explicitly names a foreign market (e.g., 'S&P 500', 'US', 'Fed').\n"
    "\n"
    "Response contract (MANDATORY):\n"
    "- Finalize your reply by calling the `return_reply` tool exactly once. Do NOT "
    "emit any plain-text reply. All final content goes into the tool arguments.\n"
    "- `answer`: in Tilly's voice (friendly, specific, plain-language), 2-3 short sentences, MAXIMUM 60 words. Cite the "
    "source inline (e.g., 'per our daily snapshot' or 'per live web search'). No "
    "preamble, no greeting, no headings like '**Answer**', no acknowledgment, no "
    "reference to prior turns, no meta commentary. Just answer the question directly.\n"
    "- `justification_bullets`: MAXIMUM 3 bullets, each ≤ 15 words. Include ONLY when "
    "the question has an investment/portfolio implication the customer might act on "
    "(e.g., 'should I buy', 'is this a good time', valuation/allocation questions). "
    "Set to null for pure factual lookups (PE ratio, repo rate, FX rate) — those get a "
    "clean one-line answer with no bullets.\n"
    "- Engage with market questions using the data sources above — don't gate the answer on missing personal data, since that's a different flow.\n"
    "- Personalization: `client_context` may include `first_name`. Use it sparingly to add warmth — at most once per response, and not every turn (repetition feels artificial). For pure factual lookups (PE ratio, repo rate, FX rate), skip the name entirely. If `first_name` is null or missing, work without it.\n"
    "- Do NOT moralize, disclaim, or list what you would need to advise further."
)

_RETURN_REPLY_TOOL = {
    "name": "return_reply",
    "description": (
        "Return the final customer-facing reply. Call this exactly once at the end "
        "of your turn. The reply is assembled by the backend from these fields; do "
        "not emit any free-text response outside this tool call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": (
                    "Conversational prose answer. 2-3 short sentences, max 60 words. "
                    "Cite source inline. No preamble, no headings, no acknowledgment, "
                    "no meta commentary — just the answer."
                ),
            },
            "justification_bullets": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "maxItems": 3,
                "description": (
                    "Up to 3 short bullets, each ≤ 15 words. Include ONLY for "
                    "investment/portfolio-implication questions (should I buy, is "
                    "this a good time, valuation/allocation calls). Set to null for "
                    "pure factual lookups — no bullets, just the one-line answer."
                ),
            },
        },
        "required": ["answer"],
    },
}


def _render_reply(answer: str, bullets: list[str] | None) -> str:
    out = answer.strip()
    if bullets:
        cleaned = [b.strip() for b in bullets if isinstance(b, str) and b.strip()]
        if cleaned:
            out += "\n\n" + "\n".join(f"- {b}" for b in cleaned)
    return out


# Anthropic web_search wraps cited passages in <cite index="...">...</cite> tags.
# Strip them before feeding the research digest into Pass 2 so they don't leak.
_CITE_TAG_RE = re.compile(r"</?cite\b[^>]*>", re.IGNORECASE)


def _strip_cite_tags(text: str) -> str:
    return _CITE_TAG_RE.sub("", text).strip()


def _enrich_inr_fields(obj):
    """Walk a dict/list, add ``*_indian`` siblings to any ``*_inr`` field.

    Indian-notation strings are pre-computed by ``format_inr_indian`` so the LLM
    never has to convert raw rupees at inference time (Haiku frequently drops
    an order of magnitude on lakh/crore boundaries). The system prompt
    instructs the LLM to copy these strings verbatim instead of converting.
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            out[k] = _enrich_inr_fields(v)
            if isinstance(k, str) and k.endswith("_inr") and v is not None:
                out[f"{k[:-4]}_indian"] = format_inr_indian(v)
        return out
    if isinstance(obj, list):
        return [_enrich_inr_fields(item) for item in obj]
    return obj


_RESEARCH_SYSTEM_PROMPT = (
    "You are the research step of an Indian-market advisor. Your job is to gather "
    "the factual data needed to answer the customer's question — NOT to write the "
    "final reply.\n"
    "\n"
    "Source priority:\n"
    "1. Use figures from the 'Market commentary context' section of the user "
    "message when they are present. Cite them as 'per our daily snapshot'.\n"
    "2. If the requested figure is NOT in the commentary, call `web_search` (up "
    "to 3 India-specific queries, e.g. 'Nifty 50 PE today', 'RBI repo rate "
    "latest', 'USD INR spot').\n"
    "3. Never recall market data from training knowledge.\n"
    "\n"
    "Output: a short plain-text factual digest (max ~150 words) of ONLY the data "
    "points relevant to the question. Do not format, do not advise, do not add a "
    "preamble, do not structure with headings — just the facts the composer will "
    "use to write the final reply."
)


async def generate_general_chat_response(
    user_question: str,
    classification: ClassificationResult,
    market_commentary: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    client_context: dict | None = None,
) -> str:
    """Generate a concise answer with justification for general/market intents."""

    # Out-of-scope: return the canned message immediately.
    if classification.intent == Intent.OUT_OF_SCOPE:
        return classification.out_of_scope_message or OUT_OF_SCOPE_MESSAGE

    api_key = get_settings().get_anthropic_general_chat_key()
    if not api_key:
        return (
            "I can't reach the language model right now — the Anthropic API key isn't "
            "configured on the server. Please set `ANTHROPIC_API_KEY` and try again."
        )

    # Truncate large market commentary to stay within prompt budget.
    commentary = (market_commentary or "")[:_MAX_COMMENTARY_CHARS]

    user_prompt = (
        f"Intent: {classification.intent.value}\n"
        f"Classifier reasoning: {classification.reasoning}\n\n"
        f"{build_history_block(conversation_history)}\n\n"
        f"User question: {user_question}\n\n"
        f"Client context from profile/portfolio DB: "
        f"{json.dumps(_enrich_inr_fields(client_context), ensure_ascii=True) if client_context else 'null'}\n\n"
        f"Market commentary context (if relevant, use it; if not relevant, ignore):\n"
        f"{commentary}"
    )

    unauthorised_reply = (
        "I couldn't reach the language model — Anthropic returned a 401 Unauthorized. "
        "Please set a valid `ANTHROPIC_API_KEY` in `.env` and restart the API server."
    )

    # --- Pass 1: research (web_search allowed, plain-text digest) ---
    research_llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        api_key=api_key,
        timeout=90.0,
    ).bind_tools([
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
    ])
    try:
        research_resp = await research_llm.ainvoke([
            SystemMessage(content=_RESEARCH_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
    except anthropic.AuthenticationError:
        return unauthorised_reply

    content = research_resp.content
    if isinstance(content, list):
        research_raw = "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        research_raw = str(content) if content else ""
    research_digest = _strip_cite_tags(research_raw)
    if not research_digest:
        research_digest = "(No additional research data — answer from the market commentary context above.)"

    # --- Pass 2: compose (forced return_reply, no tools that could derail format) ---
    compose_user_prompt = (
        f"{user_prompt}\n\n"
        f"Research digest (already gathered; do not call any tools other than "
        f"`return_reply`):\n{research_digest}"
    )
    compose_llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        api_key=api_key,
        timeout=60.0,
    ).bind_tools(
        [_RETURN_REPLY_TOOL],
        tool_choice={"type": "tool", "name": "return_reply"},
    )
    try:
        compose_resp = await compose_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=compose_user_prompt),
        ])
    except anthropic.AuthenticationError:
        return unauthorised_reply

    # Forced tool_choice → response should always have a return_reply tool_call.
    for tool_call in compose_resp.tool_calls:
        if tool_call["name"] == "return_reply":
            args = tool_call["args"] or {}
            answer = args.get("answer")
            if isinstance(answer, str) and answer.strip():
                bullets = args.get("justification_bullets")
                if not isinstance(bullets, list):
                    bullets = None
                return _render_reply(answer, bullets)
            break

    # Fallback for the rare case where the tool call is missing or malformed.
    content = compose_resp.content
    if isinstance(content, list):
        text_parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
    else:
        text_parts = [str(content)] if content else []
    fallback_text = _strip_cite_tags("".join(text_parts))
    if fallback_text:
        return fallback_text
    return (
        "I couldn't produce a reply in the expected format. Please try rephrasing your question."
    )
