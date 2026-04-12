"""AI bridge — `intent_classifier_service.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

import asyncio
import json
import logging
import os
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

_INTENT_LABELS: dict[str, str] = {
    "portfolio_optimisation": "Portfolio Optimisation",
    "goal_planning": "Goal Planning",
    "portfolio_query": "Portfolio Query",
    "general_market_query": "General Market Query",
    "out_of_scope": "Out of Scope",
}

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
                    "enum": [
                        "portfolio_optimisation",
                        "goal_planning",
                        "portfolio_query",
                        "general_market_query",
                        "out_of_scope",
                    ],
                },
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["intent", "confidence", "reasoning"],
        },
    },
}


def _get_classifier() -> IntentClassifier:
    api_key = get_settings().get_anthropic_intent_classifier_key()
    if not api_key:
        raise RuntimeError(
            "Set INTENT_CLASSIFIER_API_KEY or ANTHROPIC_API_KEY in .env for intent classification."
        )
    return IntentClassifier(api_key=api_key)


async def _classify_via_openai(
    customer_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> ClassificationResult:
    """Fallback: call OpenAI when Anthropic has no credits."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot use OpenAI fallback.")

    history_block = build_history_block(conversation_history)
    user_content = ""
    if history_block:
        user_content += history_block + "\n\n"
    user_content += (
        f"Customer's current question: {customer_question}\n\n"
        "Classify the intent using the classify_intent tool."
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
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    tool_call = data["choices"][0]["message"]["tool_calls"][0]["function"]
    raw = json.loads(tool_call["arguments"])

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
) -> ClassificationResult:
    """Try Anthropic first; fall back to OpenAI if Anthropic fails (e.g. no credits)."""
    history = [
        ConversationMessage(role=msg["role"], content=msg["content"])
        for msg in (conversation_history or [])
    ]

    try:
        classification_input = ClassificationInput(
            customer_question=customer_question,
            conversation_history=history,
        )
        classifier = _get_classifier()
        return await asyncio.to_thread(classifier.classify, classification_input)
    except Exception as exc:
        logger.warning("Anthropic classifier failed (%s), trying OpenAI fallback...", exc)

    return await _classify_via_openai(customer_question, conversation_history)


def format_intent_response(result: ClassificationResult) -> str:
    """Format the classification result as a readable assistant message."""
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
    return dict(_INTENT_LABELS)
