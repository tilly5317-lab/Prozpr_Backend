"""General chat module — fallback when no specialist module owns the intent,
and the final step of the ``general_market_query`` sequence.

This is the only AI module that lives under ``ai_engine`` itself rather than
its own domain folder: general chat isn't a *domain*, it's the orchestrator's
default. It still follows the same uniform ``run(turn, ctx, prior)`` shape.

Sequence positions:
  - "general_chat"         intent → sequence = [general_chat]
  - "general_market_query" intent → sequence = [market_commentary, general_chat]
    (we read the macro doc from ``prior[AIModule.MARKET_COMMENTARY].payload``
    and tailor the final reply with it).

Per the AI-module rule this is the ONLY place in the codebase that imports
``generate_general_chat_response``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domains.ai_engine.types import AIModule, IntentDecision, ModuleOutput

# Note: this re-export is the ONLY permitted import of the legacy general
# chat implementation. Search for ``generate_general_chat_response`` to confirm.
from app.domains.general_chat.services.general_chat_engine import (  # noqa: F401
    generate_general_chat_response as _generate_general_chat_response,
)

logger = logging.getLogger(__name__)


def _enrich_with_first_name(
    client_ctx: dict[str, Any] | None,
    user_ctx: Any,
) -> dict[str, Any] | None:
    """Inject ``first_name`` from the User ORM into a shallow copy of ``client_ctx``.

    The general-chat prompt's personalisation rule wants the customer's first
    name; the FE-supplied client_context doesn't carry it. Never mutates the
    input dict.
    """
    first_name = getattr(user_ctx, "first_name", None) if user_ctx is not None else None
    if not first_name:
        return client_ctx
    enriched = dict(client_ctx) if client_ctx else {}
    enriched["first_name"] = first_name
    return enriched


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    # Pull the raw classification from the intent classifier's ModuleOutput
    # (the brain always runs that module first) and the macro doc the
    # market_commentary module produced before us (if it was in the sequence).
    ic = prior.get(AIModule.INTENT_CLASSIFIER.value)
    intent: IntentDecision | None = (
        ic.payload if (ic and isinstance(ic.payload, IntentDecision)) else None
    )
    classification = intent.raw if intent and intent.raw is not None else None

    mc = prior.get(AIModule.MARKET_COMMENTARY.value)
    market_doc: str | None = (
        mc.payload if (mc and isinstance(mc.payload, str)) else None
    )

    client_ctx = _enrich_with_first_name(turn.client_context, turn.user_ctx)
    try:
        reply = await _generate_general_chat_response(
            user_question=turn.user_question,
            classification=classification,
            market_commentary=market_doc,
            conversation_history=turn.conversation_history,
            client_context=client_ctx,
            ctx=ctx,
        )
    except Exception:
        # Retry once without the macro doc — the agent occasionally objects
        # to an overlong attachment but answers fine on the question alone.
        logger.exception("General chat failed for session %s", turn.session_id)
        reply = await _generate_general_chat_response(
            user_question=turn.user_question,
            classification=classification,
            conversation_history=turn.conversation_history,
            client_context=client_ctx,
            ctx=ctx,
        )

    return ModuleOutput(text=reply)


__all__ = ["run"]
