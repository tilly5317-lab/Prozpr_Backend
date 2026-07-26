"""Classify user messages into intents (Anthropic only — no second provider)."""

from __future__ import annotations

import logging
import re

from app.core.config import get_settings
from app.domains.ai_engine.common import ensure_ai_agents_path, with_gap_notes

ensure_ai_agents_path()

from intent_classifier import (
    ClassificationInput,
    ClassificationResult,
    ConversationMessage,
    IntentClassifier,
)
from intent_classifier.models import Intent
from intent_classifier.prompts import OUT_OF_SCOPE_MESSAGE, STOCK_ADVICE_MESSAGE

# Prefix of the previously-canned goal_planning redirect (removed after the
# goal_planning bridge cutover). Kept so old chat sessions still get scrubbed
# from classifier history.
_LEGACY_GOAL_PLANNING_PREFIX = "Goal planning — checking whether"

# Prefixes from earlier variants of the OOS sub-reason replies (pre tone
# refresh). Kept so the history-scrub still catches them in old DB sessions.
_LEGACY_OOS_PREFIXES: tuple[str, ...] = (
    "I'm Tilly — an AI assistant from Prozpr, here to help",  # IDENTITY_OR_META
    "I can't help with passwords",  # SECURITY_OR_CREDENTIALS
    "Session summaries aren't built in yet",  # CHAT_SUMMARY
    "That's outside what I can help with",  # OFF_TOPIC
)

# Prefix of the previous generic OUT_OF_SCOPE_MESSAGE (pre tone refresh).
_LEGACY_OUT_OF_SCOPE_PREFIX = "I'm currently set up to help with asset allocation"

logger = logging.getLogger(__name__)

# Explicit rebalance phrasing (incl. common typos) → routes to the rebalancing
# intent via _apply_rebalancing_keyword_override.
_REBALANCE_UTTERANCE = re.compile(
    r"\b(?:re-?\s*balan\w*|rebalcance|relablace)\b",
    re.IGNORECASE,
)

# Human-readable labels for each intent value. Only the intents with a
# customer-visible "label" surface appear here.
_INTENT_LABELS: dict[str, str] = {
    "asset_allocation": "Portfolio Optimisation",
    "goal_planning": "Goal Planning",
    "stock_advice": "Stock Advice",
    "portfolio_query": "Portfolio Query",
    "general_market_query": "General Market Query",
    "rebalancing": "Rebalancing",
    "additional_investment": "Additional Investment",
    "out_of_scope": "Out of Scope",
}


def _get_classifier() -> IntentClassifier:
    """Build an Anthropic-backed classifier; raises if no API key is configured."""
    api_key = get_settings().get_anthropic_intent_classifier_key()
    if not api_key:
        raise RuntimeError(
            "Set INTENT_CLASSIFIER_API_KEY or ANTHROPIC_API_KEY in .env."
        )
    return IntentClassifier(api_key=api_key)


# Intents whose replies are refusal-style redirects; their turns are scrubbed
# from classifier history so the LLM doesn't anchor on refusals.
_CANNED_REDIRECT_INTENTS = frozenset({"out_of_scope", "stock_advice"})


def _strip_canned_redirect_turns(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop (user, assistant) pairs where the assistant returned a canned redirect.

    The classifier reads recent history; if a refusal-style redirect sits in
    there (out_of_scope, goal_planning-not-yet-built, or stock_advice-redirect),
    the LLM anchors on it and biases subsequent turns toward the same refusal.
    Other agents still see the full history — this trim is classifier-only.

    Detection is two-tier:
    - Rows written since chat_messages.intent shipped carry the turn's intent —
      match on the tag. This also catches the LLM-tailored redirects, whose
      text never matched any prefix.
    - Untagged rows (intent is None — pre-column history) fall back to the
      legacy message-prefix matching below. That prefix list is FROZEN: new
      copy variants must not be added — new rows are always tagged.
    """
    # Lazily import the tailored OOS replies + the goal-planning sentinel so
    # we don't pull in the LLM-call modules at classifier import time.
    from app.domains.general_chat.services.general_chat_engine import (
        _OOS_REPLIES_BY_SUBREASON,
    )
    from app.domains.ai_engine.services.brain import _GOAL_PLANNING_SENTINEL

    legacy_prefixes = (
        OUT_OF_SCOPE_MESSAGE,
        _LEGACY_OUT_OF_SCOPE_PREFIX,
        _LEGACY_GOAL_PLANNING_PREFIX,
        STOCK_ADVICE_MESSAGE,
        *(_OOS_REPLIES_BY_SUBREASON.values()),
        *_LEGACY_OOS_PREFIXES,
    )
    drop: set[int] = set()
    for i, m in enumerate(history):
        if m.get("role") != "assistant":
            continue
        tagged_intent = m.get("intent")
        if tagged_intent is not None:
            is_canned = tagged_intent in _CANNED_REDIRECT_INTENTS
        else:
            content = m.get("content") or ""
            is_canned = any(content.startswith(p) for p in legacy_prefixes) or (
                _GOAL_PLANNING_SENTINEL in content
            )
        if is_canned:
            drop.add(i)
            if i > 0 and history[i - 1].get("role") == "user":
                drop.add(i - 1)
    return [m for i, m in enumerate(history) if i not in drop]


def _apply_rebalancing_keyword_override(
    question: str,
    result: ClassificationResult,
) -> ClassificationResult:
    """Map explicit rebalance phrasing → ``rebalancing`` intent.

    A safety net for when the LLM labels "rebalance my portfolio" as
    asset_allocation / portfolio_query: an explicit rebalance utterance should
    route to the rebalancing engine, not a fresh ideal-allocation design.

    Deliberate consequence (product decision 2026-07-04): this override WINS
    over the classifier prompt's "rebalance to be more aggressive = target
    change → asset_allocation" rule whenever the word "rebalance" appears.
    Pinned by the eval-gate case ``aa/rebalance-to-be-aggressive``; the prompt
    rule still governs paraphrases without the word.
    """
    if result.intent == Intent.REBALANCING:
        return result
    if not _REBALANCE_UTTERANCE.search(question):
        return result
    if result.intent not in (Intent.ASSET_ALLOCATION, Intent.PORTFOLIO_QUERY):
        return result
    logger.info(
        "intent keyword override: %s → rebalancing (question=%r)",
        result.intent.value,
        question[:120],
    )
    return ClassificationResult(
        intent=Intent.REBALANCING,
        confidence=max(float(result.confidence), 0.9),
        reasoning=(
            f"{result.reasoning} "
            "(Adjusted: customer asked to rebalance the portfolio, not to design a new ideal allocation.)"
        ),
        out_of_scope_message=result.out_of_scope_message,
    )


async def classify_user_message(
    customer_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    active_intent: str | None = None,
) -> ClassificationResult:
    """Classify intent via Anthropic. Anthropic-only — failures propagate.

    There is deliberately no second provider. The OpenAI fallback that used to
    sit here was never configured in the deployment, and when it did run it
    degraded safety: it attached ``out_of_scope_message`` for OUT_OF_SCOPE only,
    so a ``stock_advice`` classification arrived with none. ``brain.py``'s
    classifier-only short-circuit requires that message, and ``FLOWS`` has no
    ``stock_advice`` entry — so the refusal was skipped and the question fell
    through to general_chat, which answered it. The brain already turns a raised
    classifier error into a recovery reply.

    ``active_intent`` is the intent from the most recent prior turn in this
    session (or ``None`` for first turns). When set, it biases the classifier
    toward returning the same intent on follow-ups. Raises ``ValueError`` if
    the string is not a valid ``Intent`` enum value.
    """
    filtered_history = _strip_canned_redirect_turns(conversation_history or [])
    # ConversationMessage has no timestamp field, so the age of a resumed thread
    # has to ride inside the content. Annotate AFTER the scrub, so the notes
    # describe the turns the classifier actually sees.
    history = [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in with_gap_notes(filtered_history)
    ]
    active = Intent(active_intent) if active_intent else None
    inp = ClassificationInput(
        customer_question=customer_question,
        conversation_history=history,
        active_intent=active,
    )
    result = await _get_classifier().aclassify(inp)
    return _apply_rebalancing_keyword_override(customer_question, result)


def format_intent_response(result: ClassificationResult) -> str:
    """Format a ClassificationResult as a readable assistant message."""
    label = _INTENT_LABELS.get(result.intent.value, result.intent.value)
    if result.out_of_scope_message:
        return (
            f"**Intent Detected:** {label}\n"
            f"**Confidence:** {result.confidence:.0%}\n\n"
            f"{result.out_of_scope_message}"
        )
    return (
        f"**Intent Detected:** {label}\n"
        f"**Confidence:** {result.confidence:.0%}\n"
        f"**Reasoning:** {result.reasoning}"
    )


def intent_labels() -> dict[str, str]:
    """Return a copy of the intent-to-label mapping."""
    return dict(_INTENT_LABELS)
