"""Handle general/market chat queries via OpenAI (GPT-4o-mini)."""

from __future__ import annotations

import json
import os

import httpx

from app.services.ai_bridge.common import build_history_block, ensure_ai_agents_path
from app.services.ai_bridge.intent_classifier_service import intent_labels

ensure_ai_agents_path()

from intent_classifier.models import Intent
from intent_classifier.prompts import OUT_OF_SCOPE_MESSAGE
from intent_classifier import ClassificationResult

# Keep market commentary under this limit so the prompt fits the context window.
_MAX_COMMENTARY_CHARS = 7000

_SYSTEM_PROMPT = (
    "You are AILAX, a financial assistant. Answer exactly the user's question.\n"
    "Keep response balanced in length (roughly 120-220 words).\n"
    "Return markdown with two sections only:\n"
    "1) **Answer** (direct, practical)\n"
    "2) **Justification** (2-4 bullets explaining why this answer fits question + data).\n"
    "Avoid unnecessary verbosity and avoid discussing internal intent classification."
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
        return (
            f"{classification.out_of_scope_message or OUT_OF_SCOPE_MESSAGE}\n\n"
            "**Justification**\n"
            f"- {classification.reasoning}"
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        labels = intent_labels()
        return (
            "I can help with that. Please set OPENAI_API_KEY for enhanced response quality.\n\n"
            "**Justification**\n"
            f"- Intent: {labels.get(classification.intent.value, classification.intent.value)}\n"
            f"- Reasoning: {classification.reasoning}"
        )

    # Truncate large market commentary to stay within prompt budget.
    commentary = (market_commentary or "")[:_MAX_COMMENTARY_CHARS]

    user_prompt = (
        f"Intent: {classification.intent.value}\n"
        f"Classifier reasoning: {classification.reasoning}\n\n"
        f"{build_history_block(conversation_history)}\n\n"
        f"User question: {user_question}\n\n"
        f"Client context from profile/portfolio DB: "
        f"{json.dumps(client_context, ensure_ascii=True) if client_context else 'null'}\n\n"
        f"Market commentary context (if relevant, use it; if not relevant, ignore):\n"
        f"{commentary}"
    )

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 420,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()

    return resp.json()["choices"][0]["message"]["content"]
