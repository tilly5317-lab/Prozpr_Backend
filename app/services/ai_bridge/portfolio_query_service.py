"""Answer portfolio_query intents — informational questions about the user's own holdings.

Uses Claude Haiku with a forced `return_reply` tool so the output is a clean
conversational answer targeted at the specific question asked (e.g. "biggest
holding", "equity allocation %", "how many funds"), rather than a flat dump
of every portfolio stat we have.
"""

from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.services.ai_bridge.common import build_history_block
from app.services.ai_bridge.general_chat_service import (
    _RETURN_REPLY_TOOL,
    _extract_return_reply,
    _render_reply,
    _strip_cite_tags,
)

_SYSTEM_PROMPT = (
    "You are Prozpr, an Indian-market financial assistant. The customer is asking an "
    "informational question about THEIR OWN portfolio (holdings, allocation, goals). "
    "You have their portfolio data in the user message — answer strictly from that.\n"
    "\n"
    "Rules:\n"
    "- Answer the exact question asked. Do NOT dump the full portfolio summary when "
    "the customer asks one targeted thing (e.g. 'which is my biggest holding' → name "
    "the single biggest holding with its value/%, not every holding).\n"
    "- Use the figures in the data block verbatim. Do not invent numbers, do not "
    "recall from training, do not call any tools other than `return_reply`.\n"
    "- Money formatting: use Indian notation — lakhs ('L') and crores ('Cr'). NEVER "
    "say 'million' or 'billion'. The data block already uses this format; keep it "
    "exactly as shown (e.g. 'INR 45.00 L', 'INR 3.00 Cr').\n"
    "- If the data needed to answer is genuinely not present in the data block, say "
    "so in one short sentence.\n"
    "\n"
    "Response contract (MANDATORY):\n"
    "- Finalize by calling `return_reply` exactly once.\n"
    "- `answer`: conversational prose, 1-3 short sentences, MAXIMUM 60 words. No "
    "preamble, no greeting, no '**Answer**' heading, no echoing the question back.\n"
    "- `justification_bullets`: set to null for straightforward lookups (biggest "
    "holding, fund count, total value). Use up to 3 short bullets ONLY if the "
    "customer asked something that benefits from a breakdown (e.g. 'show my "
    "allocation' → one bullet per asset class).\n"
    "- Do NOT moralize, disclaim, or recommend changes — this is informational, not "
    "advisory."
)


def _fmt_inr(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "INR —"
    if abs(v) >= 1e7:
        return f"INR {v / 1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"INR {v / 1e5:.2f} L"
    return f"INR {v:,.0f}"


def _build_portfolio_block(user) -> str:
    """Render the user's primary portfolio into a compact text block for the LLM."""
    portfolios = list(getattr(user, "portfolios", []) or [])
    if not portfolios:
        return "Portfolio: (none on file)"

    primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
    total_value = getattr(primary, "total_value", None)
    total_invested = getattr(primary, "total_invested", None)
    gain_pct = getattr(primary, "total_gain_percentage", None)

    lines = [
        "Primary portfolio:",
        f"- Current value: {_fmt_inr(total_value)}",
        f"- Total invested: {_fmt_inr(total_invested)}",
    ]
    if gain_pct is not None:
        try:
            lines.append(f"- Overall gain/loss: {float(gain_pct):.2f}%")
        except (TypeError, ValueError):
            pass

    holdings = list(getattr(primary, "holdings", []) or [])
    if holdings:
        ranked = sorted(
            holdings,
            key=lambda h: float(getattr(h, "current_value", 0) or 0),
            reverse=True,
        )
        lines.append(f"- Number of holdings: {len(holdings)}")
        lines.append("- Holdings (ranked by current value):")
        for h in ranked:
            name = getattr(h, "instrument_name", None) or getattr(h, "ticker_symbol", "Unknown")
            itype = getattr(h, "instrument_type", None)
            qty = getattr(h, "quantity", None)
            cv = getattr(h, "current_value", None)
            parts = [f"{name}"]
            if itype:
                parts.append(f"type={itype}")
            if qty is not None:
                try:
                    parts.append(f"qty={float(qty):g}")
                except (TypeError, ValueError):
                    pass
            parts.append(f"value={_fmt_inr(cv)}")
            lines.append(f"    - {', '.join(parts)}")
    else:
        lines.append("- Holdings: none recorded")

    allocs = list(getattr(primary, "allocations", []) or [])
    if allocs:
        ranked_allocs = sorted(
            allocs,
            key=lambda a: float(getattr(a, "allocation_percentage", 0) or 0),
            reverse=True,
        )
        lines.append("- Allocation by asset class:")
        for a in ranked_allocs:
            ac = getattr(a, "asset_class", "Unknown")
            try:
                pct = f"{float(getattr(a, 'allocation_percentage', 0) or 0):.1f}%"
            except (TypeError, ValueError):
                pct = "—"
            amt = _fmt_inr(getattr(a, "amount", None))
            lines.append(f"    - {ac}: {pct} ({amt})")

    goals = list(getattr(user, "financial_goals", []) or [])
    if goals:
        names = []
        for g in goals:
            nm = getattr(g, "goal_name", None) or getattr(g, "name", None)
            if nm:
                names.append(nm)
        if names:
            lines.append(f"- Financial goals on file: {', '.join(names[:5])}")

    return "\n".join(lines)


async def generate_portfolio_query_response(
    user,
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Answer the user's portfolio question via Haiku, targeted to the exact ask."""

    portfolios = list(getattr(user, "portfolios", []) or [])
    first_name = getattr(user, "first_name", None) or "there"

    # Early out: no portfolio rows at all.
    if not portfolios:
        return (
            f"Hi {first_name}, I couldn't find an active portfolio on your account yet. "
            "Once you add holdings or allocation details, I can answer questions like "
            "your biggest holding, allocation breakdown, and overall performance."
        )

    api_key = get_settings().get_anthropic_key()
    if not api_key:
        return (
            "I can't reach the language model right now — the Anthropic API key isn't "
            "configured on the server. Please set `ANTHROPIC_API_KEY` and try again."
        )

    portfolio_block = _build_portfolio_block(user)

    user_prompt = (
        f"{build_history_block(conversation_history)}\n\n"
        f"Customer question: {user_question}\n\n"
        f"Customer portfolio data (authoritative — answer only from this):\n"
        f"{portfolio_block}"
    ).strip()

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [_RETURN_REPLY_TOOL],
        "tool_choice": {"type": "tool", "name": "return_reply"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
    if resp.status_code == 401:
        return (
            "I couldn't reach the language model — Anthropic returned a 401 Unauthorized. "
            "Please set a valid `ANTHROPIC_API_KEY` in `.env` and restart the API server."
        )
    resp.raise_for_status()

    blocks = resp.json().get("content", [])
    parsed = _extract_return_reply(blocks)
    if parsed is not None:
        answer, bullets = parsed
        return _render_reply(answer, bullets)

    fallback_text = _strip_cite_tags(
        "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    )
    if fallback_text:
        return fallback_text
    return "I couldn't produce a reply in the expected format. Please try rephrasing your question."
