"""Per-intent followup handler registry + dispatcher.

Handlers register themselves via the @register(intent) decorator at import
time. ChatBrain calls dispatch_followup() when a turn is identified as a
follow-up that should narrate a prior AgentRun rather than re-run the agent.
"""

from __future__ import annotations

from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


Handler = Callable[
    ["AgentRunRecord", "TurnContext"], Awaitable[str],
]

_HANDLERS: dict[str, Handler] = {}


def register(intent: str) -> Callable[[Handler], Handler]:
    """Register a followup handler for the given intent. Stackable."""
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[intent] = fn
        return fn
    return decorator


async def dispatch_followup(
    intent: str,
    agent_run: "AgentRunRecord",
    turn_context: "TurnContext",
) -> str:
    """Look up the handler for ``intent`` and invoke it."""
    handler = _HANDLERS.get(intent)
    if handler is None:
        raise RuntimeError(
            f"No followup handler registered for intent={intent!r}"
        )
    return await handler(agent_run, turn_context)
