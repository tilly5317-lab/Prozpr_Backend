"""Asset allocation AI module — the ONLY gateway to
``AI_Agents.asset_allocation_pydantic``.

Per the AI-module rule, nothing else in the codebase imports the asset
allocation engine — they call ``run(turn, ctx, prior)`` here.

Sequence position:
  - "asset_allocation" intent → sequence = [asset_allocation]
  - "rebalancing"     intent → sequence = [asset_allocation, rebalancing]
    (the rebalancing module reads our payload from
    ``prior[AIModule.ASSET_ALLOCATION]``)

The text + persisted run id surface back to the chat reply only when this is
the LAST module in the sequence. When it's an intermediate step (rebalancing),
the rebalancing module produces the final text and this module's text is
discarded.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the asset allocation engine via the registered chat handler.

    Internals: the handler self-registers under ``"asset_allocation"`` in the
    chat dispatcher when its ``chat.py`` is imported. We do that lazy import
    here so the side-effect lands before ``dispatch_chat`` looks it up.
    """
    # Lazy imports for the @register side-effect and to keep brain startup light.
    from app.domains.asset_allocation.services.aa_engine import chat as _aa_chat  # noqa: F401
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("asset_allocation", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,                                      # carries the structured Allocation result for downstream
        persisted_run_id=result.asset_allocation_run_id,
        snapshot_id=result.snapshot_id,
        rebalancing_recommendation_id=result.rebalancing_recommendation_id,
        chart_payloads=result.chart_payloads,
    )


__all__ = ["run"]
