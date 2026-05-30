"""Market commentary module — the gateway to ``AI_Agents.market_commentary``.

Per the AI-module architectural rule, this is the ONLY place in the codebase
that touches ``generate_market_commentary``. The chat brain calls
``run(turn, ctx, prior)`` and never imports the agent directly.

Sequence position: this module produces a macro doc that the subsequent
``general_chat`` module reads from ``prior[AIModule.MARKET_COMMENTARY].payload``
when it tailors the final reply.

A 120-second timeout caps the agent call — on timeout we return an empty
payload so the sequence continues into general_chat with no macro doc rather
than failing the whole turn.
"""

from __future__ import annotations

import asyncio
import logging

from app.domains.ai_engine.services.types import ModuleOutput

# Note: this re-export is the ONLY permitted import of the legacy market
# commentary implementation. Search for ``generate_market_commentary`` to
# confirm.
from app.domains.ai_engine.services.bridges.market_commentary_service import (  # noqa: F401
    generate_market_commentary as _generate_market_commentary,
)

logger = logging.getLogger(__name__)

_MARKET_COMMENTARY_TIMEOUT_S = 120.0


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the market commentary agent.

    Returns a ``ModuleOutput`` whose ``payload`` is the macro doc string (or
    ``None`` on timeout / failure — downstream modules guard against that).
    Does NOT populate ``text`` — the subsequent ``general_chat`` module owns
    the final assistant reply for this sequence.
    """
    try:
        macro_doc = await asyncio.wait_for(
            _generate_market_commentary(
                user_question=turn.user_question,
                conversation_history=turn.conversation_history,
            ),
            timeout=_MARKET_COMMENTARY_TIMEOUT_S,
        )
        return ModuleOutput(payload=macro_doc)
    except asyncio.TimeoutError:
        logger.warning(
            "Market commentary timed out (%.0fs) session=%s; sequence will continue without macro doc",
            _MARKET_COMMENTARY_TIMEOUT_S, turn.session_id,
        )
        return ModuleOutput(payload=None)
    except Exception:
        logger.exception("Market commentary failed for session %s", turn.session_id)
        return ModuleOutput(payload=None)


__all__ = ["run"]
