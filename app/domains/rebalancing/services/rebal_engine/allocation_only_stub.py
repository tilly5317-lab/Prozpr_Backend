"""TEMP(rebalancing-chat): rebalancing intent → asset allocation only.

See ``_temp_chat_deferral.py``. Remove this module when rebalancing chat ships.
"""

from __future__ import annotations

from app.domains.asset_allocation.services.aa_engine.service import compute_allocation_result
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult
from app.domains.rebalancing.services.rebal_engine._temp_allocation_tables_md import (
    build_temp_allocation_tables_markdown,
    is_allocation_output_complete,
)
from app.domains.ai_engine.turn_context import TurnContext

_MSG_ALLOCATION_INCOMPLETE = (
    "I couldn't finish building your allocation plan. Please try again in a moment."
)

# TEMP(rebalancing-chat): short footer only — tables carry the payload.
_REBALANCING_DEFERRED_NOTE = (
    "\n\n_Trade-level rebalancing is not in chat yet. Use **View plan** for details._"
)


async def handle_rebalancing_as_allocation_only(ctx: TurnContext) -> ChatHandlerResult:
    """Run asset allocation end-to-end, then return table markdown once complete."""
    outcome = await compute_allocation_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        persist_recommendation=ctx.db is not None,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="rebalance_chained",
        chat_ctx=ctx,
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None or not is_allocation_output_complete(outcome.result):
        return ChatHandlerResult(text=_MSG_ALLOCATION_INCOMPLETE)

    if ctx.db is not None and outcome.allocation_snapshot_id is None:
        return ChatHandlerResult(text=_MSG_ALLOCATION_INCOMPLETE)

    text = build_temp_allocation_tables_markdown(outcome.result)
    if not text.strip():
        return ChatHandlerResult(text=_MSG_ALLOCATION_INCOMPLETE)

    return ChatHandlerResult(
        text=text + _REBALANCING_DEFERRED_NOTE,
        snapshot_id=outcome.allocation_snapshot_id,
        asset_allocation_run_id=outcome.asset_allocation_run_id,
        rebalancing_recommendation_id=None,
    )
