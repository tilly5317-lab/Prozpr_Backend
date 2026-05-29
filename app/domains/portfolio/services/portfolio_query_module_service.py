"""Portfolio query AI module — the ONLY gateway to
``AI_Agents.portfolio_query``.

Per the AI-module rule, nothing else in the codebase imports the portfolio
query orchestrator — they call ``run(turn, ctx, prior)`` here.

Sequence position: standalone — ``portfolio_query`` intent runs only this
module. Read-only; never persists.
"""

from __future__ import annotations

from app.domains.ai_engine.services.types import ModuleOutput

# Note: this re-export is the ONLY permitted import of the legacy portfolio
# query implementation. Search for ``generate_portfolio_query_response`` to
# confirm.
from app.domains.ai_engine.services.bridges.portfolio_query_service import (  # noqa: F401
    generate_portfolio_query_response as _generate_portfolio_query_response,
)


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    content = await _generate_portfolio_query_response(
        user=turn.user_ctx,
        user_question=turn.user_question,
        conversation_history=turn.conversation_history,
    )
    return ModuleOutput(text=content)


__all__ = ["run"]
