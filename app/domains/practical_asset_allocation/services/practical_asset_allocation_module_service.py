"""Practical asset allocation AI module — the ONLY gateway to
``AI_Agents.practical_asset_allocation``.

Per the AI-module rule, nothing else in the codebase imports the practical
allocation engine — they call ``run(turn, ctx, prior)`` here.

Sequence position:
  - "rebalancing" intent → sequence = [practical_asset_allocation, rebalancing]
    (the rebalancing module reads our payload from
    ``prior[AIModule.ASSET_ALLOCATION]`` — practical allocation takes the
    asset-allocation slot in this flow).

The text + ids surface back to the chat reply only when this is the LAST module
in the sequence. As an intermediate step (rebalancing), the rebalancing module
produces the final text and this module's text is discarded.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the practical allocation engine via the registered chat handler.

    The handler self-registers under ``"practical_asset_allocation"`` in the
    chat dispatcher when its ``chat.py`` is imported. We do that lazy import
    here so the side-effect lands before ``dispatch_chat`` looks it up.
    """
    from app.domains.practical_asset_allocation.services.paa_engine import (  # noqa: F401
        chat as _paa_chat,
    )
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("practical_asset_allocation", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,                                      # carries the structured result for downstream
        persisted_run_id=result.asset_allocation_run_id,
        snapshot_id=result.snapshot_id,
        rebalancing_recommendation_id=result.rebalancing_recommendation_id,
        chart_payloads=result.chart_payloads,
    )


__all__ = ["run"]
