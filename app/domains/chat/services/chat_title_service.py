"""Generate a short, content-based title for a chat session from its first turn.

Hybrid strategy: a single cheap Haiku call (via ``langchain-anthropic``) turns the
customer's first message + the detected intent into a 3-6 word Title-Case title.
Everything is best-effort — on a missing key, timeout, or any error we fall back
to a deterministic label (intent + a trimmed snippet of the message) so a session
is never left without a sensible name and titling never blocks/fails the reply.

Called once, on a session's first turn (see ``chat_router.send_message``).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TITLE_MODEL = "claude-haiku-4-5-20251001"
_TITLE_TIMEOUT_S = 6.0
_MAX_TITLE_LEN = 60
# Structured output forces a `_ChatTitle` tool call; the cap must cover the
# tool-call JSON framing *and* the title text. 40 was too low — the call was
# truncated mid-tool-call (empty args → validation error → always fell back).
_TITLE_MAX_TOKENS = 256

# Human labels for the classifier intents, used by the deterministic fallback.
_INTENT_LABELS: dict[str, str] = {
    "asset_allocation": "Asset allocation",
    "rebalancing": "Rebalancing",
    "goal_planning": "Goal planning",
    "portfolio_query": "Portfolio",
    "general_market_query": "Market",
    "general_chat": "Chat",
    "stock_advice": "Stocks",
    "out_of_scope": "Chat",
}

_SYSTEM_PROMPT = (
    "You write a concise title for a customer's chat with an Indian financial "
    "advisory app. Given the customer's first message and the detected topic, "
    "return a 3-6 word title that captures what they want. Use Title Case, no "
    "trailing punctuation, no surrounding quotes, no emoji. Examples: "
    "'Mid Cap Sell & Tax', 'Retirement Corpus Plan', 'Rebalance My Portfolio', "
    "'Why So Much Equity'."
)


class _ChatTitle(BaseModel):
    title: str = Field(description="A 3-6 word Title Case chat title.")


def _clean(text: str) -> str:
    cleaned = " ".join((text or "").split()).strip(" \"'.-—:")
    if len(cleaned) > _MAX_TITLE_LEN:
        cleaned = cleaned[:_MAX_TITLE_LEN].rstrip() + "…"
    return cleaned


def _fallback_title(first_message: str, intent_name: str | None) -> str:
    """Deterministic title: intent label + a short snippet of the first message."""
    label = _INTENT_LABELS.get((intent_name or "").strip().lower())
    snippet = " ".join((first_message or "").split())
    if snippet:
        snippet = " ".join(snippet.split(" ")[:8])
        return _clean(f"{label} — {snippet}" if label else snippet)
    return label or "New chat"


async def generate_chat_title(first_message: str, intent_name: str | None) -> str:
    """Best-effort short title for a chat's first turn.

    Never raises — returns the deterministic fallback on any failure so the
    caller can assign the result unconditionally.
    """
    fallback = _fallback_title(first_message, intent_name)
    message = (first_message or "").strip()
    if not message:
        return fallback

    api_key = get_settings().get_anthropic_general_chat_key()
    if not api_key:
        return fallback

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatAnthropic(
            model=_TITLE_MODEL,
            api_key=api_key,
            max_tokens=_TITLE_MAX_TOKENS,
            temperature=0,
        ).with_structured_output(_ChatTitle)
        user_block = (
            f"Detected topic: {intent_name or 'general'}\n"
            f"Customer's first message:\n{message[:600]}"
        )
        # Native async so the timeout actually cancels the HTTP call (a
        # thread-offloaded invoke would keep running after wait_for gives up).
        result = await asyncio.wait_for(
            llm.ainvoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=user_block),
                ],
            ),
            timeout=_TITLE_TIMEOUT_S,
        )
        title = _clean(getattr(result, "title", "") or "")
        return title or fallback
    except Exception as exc:  # noqa: BLE001 — titling is best-effort, never fatal
        logger.warning("chat title generation failed (%s); using fallback", exc)
        return fallback
