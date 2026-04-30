"""Classify user messages into intents (Anthropic primary, OpenAI fallback)."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.config import get_settings
from app.services.ai_bridge.common import build_history_block, ensure_ai_agents_path

ensure_ai_agents_path()

from intent_classifier import (
    ClassificationInput,
    ClassificationResult,
    ConversationMessage,
    IntentClassifier,
)
from intent_classifier.models import Intent
from intent_classifier.prompts import OUT_OF_SCOPE_MESSAGE, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Human-readable labels for each intent value.
_INTENT_LABELS: dict[str, str] = {
    "portfolio_optimisation": "Portfolio Optimisation",
    "goal_planning": "Goal Planning",
    "portfolio_query": "Portfolio Query",
    "general_market_query": "General Market Query",
    "out_of_scope": "Out of Scope",
}

# OpenAI function-calling schema used in the fallback classifier.
_OPENAI_FUNCTION = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "Classify the customer's question into one of the defined intent categories.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": list(_INTENT_LABELS.keys()),
                },
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["intent", "confidence", "reasoning"],
        },
    },
}


def _get_classifier() -> IntentClassifier:
    """Build an Anthropic-backed classifier; raises if no API key is configured."""
    api_key = get_settings().get_anthropic_intent_classifier_key()
    if not api_key:
        raise RuntimeError(
            "Set INTENT_CLASSIFIER_API_KEY or ANTHROPIC_API_KEY in .env."
        )
    return IntentClassifier(api_key=api_key)


async def _classify_via_openai(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> ClassificationResult:
    """Fallback classifier using OpenAI function-calling."""
    api_key = get_settings().get_openai_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set — cannot use OpenAI fallback. "
            "Add it to .env (see https://platform.openai.com/api-keys) and restart uvicorn."
        )

    history_block = build_history_block(history)
    user_content = (
        (history_block + "\n\n" if history_block else "")
        + f"Customer's current question: {question}\n\n"
        + "Classify the intent using the classify_intent tool."
    )

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 256,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [_OPENAI_FUNCTION],
        "tool_choice": {"type": "function", "function": {"name": "classify_intent"}},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code == 401:
        raise RuntimeError(
            "OpenAI rejected the API key (401). Regenerate the key at "
            "https://platform.openai.com/api-keys , set OPENAI_API_KEY in .env (no extra spaces or quotes), "
            "and restart the server (get_settings is cached)."
        )
    resp.raise_for_status()

    raw = json.loads(
        resp.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )
    intent = Intent(raw["intent"])
    return ClassificationResult(
        intent=intent,
        confidence=float(raw["confidence"]),
        reasoning=raw["reasoning"],
        out_of_scope_message=OUT_OF_SCOPE_MESSAGE if intent == Intent.OUT_OF_SCOPE else None,
    )


async def classify_user_message(
    customer_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    active_intent: str | None = None,
) -> ClassificationResult:
    """Classify intent via Anthropic; falls back to OpenAI on failure.

    ``active_intent`` is the intent from the most recent prior turn in this
    session (or ``None`` for first turns). When set, it biases the classifier
    toward returning the same intent on follow-ups. Raises ``ValueError`` if
    the string is not a valid ``Intent`` enum value.
    """
    history = [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in (conversation_history or [])
    ]
    active = Intent(active_intent) if active_intent else None
    try:
        inp = ClassificationInput(customer_question=customer_question, conversation_history=history, active_intent=active)
        return await asyncio.to_thread(_get_classifier().classify, inp)
    except Exception as exc:
        logger.warning("Anthropic classifier failed (%s), trying OpenAI fallback...", exc)

    # NOTE: active_intent is intentionally not forwarded to the OpenAI fallback;
    # the bias is a small loss when the system is already degraded to recovery.
    return await _classify_via_openai(customer_question, conversation_history)


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
