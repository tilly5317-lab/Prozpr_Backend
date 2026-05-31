"""Rebalancing AI module — the ONLY gateway to ``AI_Agents.Rebalancing``.

Per the AI-module rule, nothing else in the codebase imports the rebalancing
engine — they call ``run(turn, ctx, prior)`` here.

Sequence position: the brain runs this AFTER ``asset_allocation`` for the
"rebalancing" intent so the engine has a fresh target allocation to rebalance
towards. We read that target from
``prior[AIModule.ASSET_ALLOCATION].payload`` (the underlying chat handler
also caches via ``cached_allocation`` for now; that's a follow-up cleanup).

TEMP: when ``REBALANCING_ENGINE_ENABLED=false`` we surface the asset
allocation result as the final reply instead of running the rebalancing
engine — see ``_temp_chat_deferral``.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domains.ai_engine.types import AIModule, ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    # TEMP — engine gated off. Surface the asset allocation output (from
    # prior_outputs, written by the asset_allocation module that ran before us)
    # rather than re-running it.
    from app.domains.rebalancing.services.rebal_engine._temp_chat_deferral import (
        TEMP_REBALANCING_RUN_ALLOCATION_ONLY,
    )
    if (
        TEMP_REBALANCING_RUN_ALLOCATION_ONLY
        and not get_settings().rebalancing_engine_enabled()
    ):
        aa = prior.get(AIModule.ASSET_ALLOCATION.value)
        if aa is not None:
            return aa
        # Asset allocation didn't run for some reason — fall back to the stub
        # that runs it inline. Same single-place-imports-AI rule applies via the
        # asset_allocation module service.
        from app.domains.rebalancing.services.rebal_engine.allocation_only_stub import (
            handle_rebalancing_as_allocation_only,
        )
        result = await handle_rebalancing_as_allocation_only(ctx)
        return ModuleOutput(
            text=result.text,
            payload=result,
            persisted_run_id=result.asset_allocation_run_id,
            snapshot_id=result.snapshot_id,
            rebalancing_recommendation_id=result.rebalancing_recommendation_id,
            chart_payloads=result.chart_payloads,
        )

    # Engine on — dispatch through the registered handler.
    from app.domains.rebalancing.services.rebal_engine import chat as _rb_chat  # noqa: F401
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("rebalancing", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,
        persisted_run_id=result.asset_allocation_run_id,
        snapshot_id=result.snapshot_id,
        rebalancing_recommendation_id=result.rebalancing_recommendation_id,
        chart_payloads=result.chart_payloads,
    )


__all__ = ["run"]
