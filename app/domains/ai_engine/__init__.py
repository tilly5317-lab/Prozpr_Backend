"""ai_engine domain — the chat brain.

This domain owns ONLY the orchestration of a chat turn, not the per-intent
work. ``services/`` holds exactly two files:

    brain.py   ChatBrain.run_turn — classify intent, run the matching flow
    flow.py    FLOWS — intent name -> ordered sequence of domain functions

The per-intent logic lives in each owning domain (asset_allocation,
rebalancing, cashflow, portfolio, market_commentary, intent_classifier,
general_chat). The brain just sequences calls to them.

The modules at this package root are the shared chat *kernel* used across
domains — contracts/utilities, not domain logic: ``types`` (ModuleOutput /
IntentDecision / AIModule), ``chat_types`` (ChatTurnInput / ChatBrainResult),
``turn_context``, ``common``, ``classifier_llm``, ``chat_dispatcher``,
``answer_formatter/``, ``visualizations/``.

Public surface::

    from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult

``ChatBrain.run_turn`` is the one entry point for a chat turn: it builds the
``TurnContext``, classifies intent, runs the matching flow, and returns a
``ChatBrainResult`` ready for the HTTP layer to ship as the assistant reply.
"""

from app.domains.ai_engine.services.brain import ChatBrain
from app.domains.ai_engine.types import IntentDecision  # noqa: F401  (re-export)
from app.domains.ai_engine.chat_types import ChatBrainResult, ChatTurnInput

__all__ = ["ChatBrain", "ChatBrainResult", "ChatTurnInput", "IntentDecision"]
