"""Chat handler for the practical_asset_allocation module.

Practical asset allocation only ever runs as the FIRST step of the rebalancing
flow — it produces the holdings-aware target the rebalancing engine rebalances
to. So this handler implements the single first-turn compute path; the
narrate / educate / counterfactual follow-up modes live in the asset_allocation
domain, which owns the standalone allocation intent.

Registered under ``"practical_asset_allocation"`` so
``practical_asset_allocation_module_service.run`` can reach it through the
shared chat dispatcher.
"""

from __future__ import annotations

import logging

from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    build_practical_fallback_brief,
    compute_practical_allocation_result,
)

logger = logging.getLogger(__name__)


@register("practical_asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the practical allocation engine and return a chat brief."""
    outcome = await compute_practical_allocation_result(
        ctx.user_ctx, ctx.user_question, chat_ctx=ctx,
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't produce a practical allocation right now. Please try again."
        )
    return ChatHandlerResult(text=build_practical_fallback_brief(outcome.result))
