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
from app.domains.practical_asset_allocation.services.practical_allocation_persist_service import (
    persist_practical_allocation_run,
)
from app.domains.profile.services.preference_tagging import (
    preference_id_for,
)

logger = logging.getLogger(__name__)


@register("practical_asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the practical allocation engine, persist the run, and return a brief."""
    outcome = await compute_practical_allocation_result(
        ctx.user_ctx,
        ctx.user_question,
        chat_ctx=ctx,
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't produce a practical allocation right now. Please try again."
        )

    # Persist every run that produced a result. Best-effort: a persistence
    # failure is logged loudly (so it surfaces in alerts) but never blocks the
    # chat reply or the downstream rebalancing step. Flush only — the chat
    # router owns the commit.
    if ctx.db is not None:
        try:
            await persist_practical_allocation_run(
                ctx.db,
                user_id=ctx.effective_user_id,
                output=outcome.result,
                chat_session_id=ctx.session_id,
                user_question=ctx.user_question,
                saved_investment_preference_id=preference_id_for(
                    ctx.user_ctx,
                    applied=outcome.result.human_override_applied is not None,
                ),
            )
        except Exception:
            logger.exception(
                "Failed to persist practical_asset_allocation run for session=%s — "
                "returning reply anyway; investigate",
                ctx.session_id,
            )

    return ChatHandlerResult(text=build_practical_fallback_brief(outcome.result))
