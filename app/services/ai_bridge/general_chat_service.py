"""AI bridge — `general_chat_service.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


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


def _compact_market_commentary(text: str, max_chars: int = 7000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Truncated for response generation]"


async def generate_general_chat_response(
    user_question: str,
    classification: ClassificationResult,
    market_commentary: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    client_context: dict | None = None,
) -> str:
    """Generate concise answer with explicit justifications."""
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

    history_block = build_history_block(conversation_history)
    commentary_block = _compact_market_commentary(market_commentary or "")
    context_block = json.dumps(client_context, ensure_ascii=True) if client_context else "null"
    system_prompt = (
        "You are AILAX, a financial assistant. Answer exactly the user's question.\n"
        "Keep response balanced in length (roughly 120-220 words).\n"
        "Return markdown with two sections only:\n"
        "1) **Answer** (direct, practical)\n"
        "2) **Justification** (2-4 bullets explaining why this answer fits question + data).\n"
        "Avoid unnecessary verbosity and avoid discussing internal intent classification."
    )
    labels = intent_labels()
    user_prompt = (
        f"Intent: {classification.intent.value}\n"
        f"Classifier reasoning: {classification.reasoning}\n\n"
        f"{history_block}\n\n"
        f"User question: {user_question}\n\n"
        f"Client context from profile/portfolio DB: {context_block}\n\n"
        "Market commentary context (if relevant, use it; if not relevant, ignore):\n"
        f"{commentary_block}"
    )

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 420,
        "messages": [
            {"role": "system", "content": system_prompt},
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
    data = resp.json()
    return data["choices"][0]["message"]["content"]
