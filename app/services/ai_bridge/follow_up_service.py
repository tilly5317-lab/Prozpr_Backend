"""Answer meta follow-up questions — questions about something Prozpr itself said earlier.

Routed here when the intent classifier flags ``follow_up_type == "meta"`` (e.g. 'why did
you suggest X', 'explain that last point'). Answers strictly from the conversation
history so we do not re-run an allocation engine or a web search just to re-explain a
prior assistant turn. Uses Claude Haiku with a forced ``return_reply`` tool.

The system prompt is assembled as ``_UNIVERSAL_BASE + _INTENT_CLAUSES[prior_intent]``
so the same service handles meta follow-ups across portfolio-optimisation, market, and
portfolio-query topics without duplication.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.services.ai_bridge.common import build_history_block, ensure_ai_agents_path
from app.services.ai_bridge.general_chat_service import (
    _RETURN_REPLY_TOOL,
    _extract_return_reply,
    _render_reply,
    _strip_cite_tags,
)

ensure_ai_agents_path()

from intent_classifier import Intent


_UNIVERSAL_BASE = (
    "You are Prozpr, an Indian-market financial assistant. The customer is asking a "
    "follow-up question about something YOU (the assistant) said earlier — for example "
    "'why did you say X', 'why so much in Y', 'explain that point', 'what did you mean'.\n"
    "\n"
    "Your job is to EXPLAIN your prior statement. Anchor your answer on the earliest "
    "assistant turn in the Recent Conversation History where the subject matter was "
    "actually stated (the turn with the recommendation, the market figure, the holdings "
    "summary, etc.). That is your ANCHOR TURN.\n"
    "\n"
    "How to read the history:\n"
    "- Find the anchor turn. Later assistant turns that deflect or give evasive "
    "non-answers on the same topic are earlier buggy replies — do not copy their "
    "framing or tone.\n"
    "- Treat all figures, names, dates, percentages, labels, and quoted phrases in the "
    "anchor turn as verbatim facts. Keep them exactly as written.\n"
    "\n"
    "How to explain the 'why':\n"
    "- Own what you said. If you recommended X, explain X. If you stated a market "
    "figure, explain the figure. If you highlighted a holding, explain why it was "
    "highlighted.\n"
    "- Use your general knowledge of the subject matter (what an instrument IS, what a "
    "market indicator MEASURES, what a portfolio metric REPRESENTS) to describe the "
    "REASONING behind the prior statement. This is descriptive explanation grounded in "
    "well-known finance concepts — it is NOT inventing numbers.\n"
    "- Do NOT compute new figures, do NOT call web_search, do NOT re-run an allocation "
    "engine, do NOT recall fresh market data from training.\n"
    "\n"
    "Hard rules:\n"
    "- NEVER say 'I'm not suggesting X', 'that was just a template', 'your actual "
    "holdings don't include X', 'it was only hypothetical', or any similar denial of "
    "what you actually said. If the anchor turn said it, you said it — explain it.\n"
    "- NEVER redirect to 'your current portfolio' or 'your current holdings' when the "
    "question is about a prior recommendation, figure, or statement. Those are "
    "different questions and the customer did not ask them.\n"
    "- If the anchor turn genuinely lacks the context needed to explain the 'why' "
    "(rare), give one honest sentence about the typical role of the subject matter "
    "and offer to answer fresh if the customer re-asks with more detail.\n"
    "- Money formatting: Indian notation — lakhs ('L') and crores ('Cr'). Never "
    "'million' or 'billion'.\n"
    "\n"
    "Response contract (MANDATORY):\n"
    "- Finalize by calling `return_reply` exactly once.\n"
    "- `answer`: conversational prose, 2-4 short sentences, MAXIMUM 80 words. Explain "
    "directly — no preamble, no greeting, no heading, no echoing the question.\n"
    "- `justification_bullets`: up to 3 short bullets when the rationale has multiple "
    "distinct components (e.g. goal fit + tax efficiency + volatility fit). Null when "
    "a single clean sentence is enough."
)


_PORTFOLIO_OPTIMISATION_CLAUSE = (
    "Topic context — this follow-up is about a prior ALLOCATION RECOMMENDATION.\n"
    "- Name the specific goal(s) from the anchor turn that the instrument is serving. "
    "Use the exact goal names as written (e.g. 'International Vacation', "
    "'Retirement', 'Child's Higher Education'), with their horizon in months/years "
    "and target amount. Do NOT fall back to generic phrasing like 'your medium-term "
    "goals' when a specifically named goal exists in the history.\n"
    "- If the instrument funds ONE goal, name it and tie the instrument's properties "
    "(tax treatment, volatility, horizon fit) to that goal. If it spans MULTIPLE "
    "goals through a shared debt/equity sleeve, name each relevant goal and explain "
    "the role the instrument plays across them.\n"
    "- Draw on general knowledge of the instrument category (what an Arbitrage Fund / "
    "Large Cap Fund / Corporate Bond Fund / Gold ETF / Liquid Fund IS and when it's "
    "typically used) to explain the fit between instrument and goal."
)


_GENERAL_MARKET_QUERY_CLAUSE = (
    "Topic context — this follow-up is about a prior MARKET STATEMENT (a figure, "
    "valuation call, macro view, or market commentary line you gave).\n"
    "- Quote or paraphrase the exact figure from the anchor turn (e.g. 'Nifty 50 at "
    "21.4x trailing PE', 'RBI repo rate at 5.25%', 'USD/INR at 90.25') and, when the "
    "anchor turn cited one, name the source you cited then ('per our daily snapshot' "
    "/ 'per live web search').\n"
    "- Explain the market concept behind the reasoning — what the indicator measures, "
    "why the reading matters, where the fair-value band sits historically — drawing "
    "on general market knowledge.\n"
    "- Do NOT call web_search to re-verify. Do NOT offer a fresh number. Use the "
    "figure from the anchor turn as given."
)


_PORTFOLIO_QUERY_CLAUSE = (
    "Topic context — this follow-up is about a prior HOLDINGS / PORTFOLIO DATA "
    "statement (what the customer owns, how it's allocated, biggest holding, "
    "equity %, etc.).\n"
    "- Name the specific metric the anchor turn used (e.g. biggest holding = highest "
    "current value; equity allocation = sum of equity sleeves; overall gain = "
    "current_value / total_invested) and the exact fund / asset names and figures "
    "from that turn.\n"
    "- Explain how the metric is computed in plain language. Do NOT add fresh "
    "holdings data or recompute; work only from what the anchor turn stated."
)


_INTENT_CLAUSES: dict[Intent, str] = {
    Intent.PORTFOLIO_OPTIMISATION: _PORTFOLIO_OPTIMISATION_CLAUSE,
    Intent.GENERAL_MARKET_QUERY:   _GENERAL_MARKET_QUERY_CLAUSE,
    Intent.PORTFOLIO_QUERY:        _PORTFOLIO_QUERY_CLAUSE,
}


def _build_system_prompt(prior_intent: Intent | None) -> str:
    clause = _INTENT_CLAUSES.get(prior_intent) if prior_intent else None
    if clause:
        return f"{_UNIVERSAL_BASE}\n\n---\n\n{clause}"
    return _UNIVERSAL_BASE


async def generate_follow_up_response(
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    prior_intent: Intent | None = None,
) -> str:
    """Answer a meta follow-up via Haiku, grounded strictly in conversation history.

    ``prior_intent`` selects a topic-specific clause appended to the universal base
    prompt so the same service handles optimisation, market, and portfolio-query
    follow-ups with the right framing.
    """

    if not conversation_history:
        return (
            "I don't have earlier turns to refer back to in this conversation yet. "
            "Could you rephrase your question with the details you'd like me to address?"
        )

    api_key = get_settings().get_anthropic_key()
    if not api_key:
        return (
            "I can't reach the language model right now — the Anthropic API key isn't "
            "configured on the server. Please set `ANTHROPIC_API_KEY` and try again."
        )

    system_prompt = _build_system_prompt(prior_intent)
    user_prompt = (
        f"{build_history_block(conversation_history)}\n\n"
        f"Customer follow-up question: {user_question}\n\n"
        f"Answer from the conversation history above. Do not invent new figures."
    ).strip()

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "system": system_prompt,
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
