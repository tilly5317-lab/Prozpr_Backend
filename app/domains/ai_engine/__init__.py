"""ai_engine domain — bridges to AI_Agents.

Hosts the chat orchestrator (``ChatBrain``), the intent router (the dispatch
switch), the per-intent bridges that delegate to ``AI_Agents`` orchestrators,
the intent-classifier bridge, and the answer formatter.

Public surface::

    from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult

``ChatBrain.run_turn`` is the one entry point for a chat turn: it builds the
``TurnContext``, classifies intent, runs the matching bridge, and returns a
``ChatBrainResult`` ready for the HTTP layer to ship as the assistant reply.
"""

from app.domains.ai_engine.services.chat_orchestrator.brain import ChatBrain

# The canonical DTOs still live in ``app.domains.ai_engine.services.chat_orchestrator`` (kept there to
# avoid breaking ~25 import sites that read them today). Re-exporting here so
# callers can use the new public surface uniformly.
from app.domains.ai_engine.services.chat_orchestrator.types import ChatBrainResult, ChatTurnInput

__all__ = ["ChatBrain", "ChatBrainResult", "ChatTurnInput"]
