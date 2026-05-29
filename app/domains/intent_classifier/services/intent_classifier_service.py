"""Intent classifier — the gateway to ``AI_Agents.intent_classifier``.

Per the AI-module architectural rule, this is the ONLY place in the codebase
that touches the underlying classifier orchestrator. The chat brain calls
``run(turn, ctx, prior)`` (the uniform module-service signature) and never
imports the orchestrator directly.

Sequence position: the intent classifier is implicitly the FIRST module the
brain runs every turn. Its ``ModuleOutput.payload`` is the ``IntentDecision``
the brain uses to pick the rest of the sequence. Downstream modules read
``prior[AIModule.INTENT_CLASSIFIER].payload`` when they need the full
classifier shape (e.g. ``general_chat`` for sentiment slots).
"""

from __future__ import annotations

from app.domains.ai_engine.services.types import IntentDecision, ModuleOutput

# Note: this re-export is the ONLY permitted import of the legacy classifier
# implementation. Search for ``classify_user_message`` to confirm.
from app.domains.ai_engine.services.bridges.intent_classifier_service import (  # noqa: F401
    classify_user_message as _classify_user_message,
    format_intent_response,
    intent_labels,
)


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Classify the user's message and return the result as a ``ModuleOutput``.

    ``ModuleOutput.payload`` carries the ``IntentDecision``. ``text`` stays
    ``None`` — the classifier is never the last module in a sequence so it
    doesn't produce a user-visible reply.
    """
    classification = await _classify_user_message(
        customer_question=turn.user_question,
        conversation_history=turn.conversation_history,
        active_intent=ctx.active_intent,
    )
    decision = IntentDecision(
        name=classification.intent.value,
        confidence=classification.confidence,
        reasoning=classification.reasoning,
        raw=classification,
    )
    return ModuleOutput(payload=decision)


__all__ = ["run", "format_intent_response", "intent_labels"]
