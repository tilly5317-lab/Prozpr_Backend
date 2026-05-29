"""Chat orchestrator — ``ChatBrain.run_turn`` is the entry point for one chat turn.

Public surface re-exports ``ChatBrain``, ``ChatTurnInput``, and
``ChatBrainResult`` so consumers can import everything from one place::

    from app.domains.ai_engine.services.chat_orchestrator import (
        ChatBrain, ChatTurnInput, ChatBrainResult,
    )
"""

from app.domains.ai_engine.services.chat_orchestrator.brain import (
    ChatBrain,
    _GOAL_PLANNING_SENTINEL,
)
from app.domains.ai_engine.services.chat_orchestrator.types import (
    ChatBrainResult,
    ChatTurnInput,
)

__all__ = [
    "ChatBrain",
    "ChatBrainResult",
    "ChatTurnInput",
    "_GOAL_PLANNING_SENTINEL",
]
