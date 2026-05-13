"""Chat handler for the ``asset_allocation`` intent — stub (engine offline)."""

from __future__ import annotations

import logging

from app.services.ai_bridge.asset_allocation.service import (
    MSG_ALLOCATION_MISSING_DOB,
    MSG_ALLOCATION_UPGRADING,
)
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.common import trace_line
from app.services.chat_core.turn_context import TurnContext

logger = logging.getLogger(__name__)


@register("asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Return a safe message without running the old pipeline."""
    trace_line("asset_allocation_chat: stub handler (engine offline)")

    if getattr(ctx.user_ctx, "date_of_birth", None) is None:
        return ChatHandlerResult(text=MSG_ALLOCATION_MISSING_DOB)

    logger.info("asset_allocation_chat stub: returning upgrade notice")
    return ChatHandlerResult(text=MSG_ALLOCATION_UPGRADING)
