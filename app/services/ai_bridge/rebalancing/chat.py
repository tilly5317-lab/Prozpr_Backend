"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import logging

from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
from app.services.chat_core.turn_context import TurnContext

logger = logging.getLogger(__name__)


@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the rebalancing pipeline for the current turn and forward the result."""
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
    )
    if outcome.blocking_message is not None:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
            chart=None,
        )
    return ChatHandlerResult(
        text=outcome.formatted_text or "",
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
        chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
    )
