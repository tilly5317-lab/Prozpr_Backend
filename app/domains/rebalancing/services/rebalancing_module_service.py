"""Rebalancing AI module — the ONLY gateway to ``AI_Agents.Rebalancing``.

Per the AI-module rule, nothing else in the codebase imports the rebalancing
engine — they call ``run(turn, ctx, prior)`` here.

Sequence position: the brain runs this AFTER ``practical_asset_allocation`` for
the "rebalancing" intent. The rebalancing engine is self-contained — it runs
the practical (holdings-aware) allocation as its own first step and lifts those
per-subgroup targets onto the held fund rows — so it does not consume ``prior``;
the upstream module's payload is informational only.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the rebalancing engine via the registered chat handler.

    The handler self-registers under ``"rebalancing"`` in the chat dispatcher
    when its ``chat.py`` is imported. We do that lazy import here so the
    side-effect lands before ``dispatch_chat`` looks it up.
    """
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
