"""Additional-investment AI module — the ONLY gateway to the
additional-investment engine.

Per the AI-module rule, nothing else in the codebase imports the
additional-investment engine — they call ``run(turn, ctx, prior)`` here.

Sequence position: the brain runs this AFTER ``practical_asset_allocation`` for
the "additional_investment" intent (``flow_additional_investment`` =
[asset_allocation, additional_investment]). The additional-investment engine
runs the practical (holdings-aware) allocation as its own first step and lifts
those per-subgroup targets onto the deploy plan, so it does not consume
``prior`` — the upstream module's payload is informational only.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the additional-investment engine via the registered chat handler.

    The handler self-registers under ``"additional_investment"`` in the chat
    dispatcher when its ``chat.py`` is imported. We do that lazy import here so
    the side-effect lands before ``dispatch_chat`` looks it up.
    """
    # Lazy imports for the @register side-effect and to keep brain startup light.
    from app.domains.additional_investment.services.ainv_engine import chat as _ainv_chat  # noqa: F401
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("additional_investment", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,  # the structured additional-investment chat result for the HTTP layer
        persisted_run_id=None,  # 3a: AdditionalInvestmentRun persistence lands in 3b
        chart_payloads=result.chart_payloads,  # forward hook: the ainv engine does not populate this yet
    )


__all__ = ["run"]
