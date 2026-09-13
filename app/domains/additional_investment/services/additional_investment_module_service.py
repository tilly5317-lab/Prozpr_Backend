"""Additional-investment AI module — the ONLY gateway to the
additional-investment engine.

Per the AI-module rule, nothing else in the codebase imports the
additional-investment engine — they call ``run(turn, ctx, prior)`` here.

Sequence position: the "additional_investment" intent maps to a SINGLE-STEP flow
(``flow_additional_investment`` = [additional_investment]); there is no upstream
module, so ``prior`` is unused. The additional-investment orchestrator self-primes
the practical (holdings-aware) allocation inline — running it once and persisting
it inline to capture ``source_allocation_run_id`` — then lifts those per-subgroup
targets onto the deploy plan.
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
        # The run IS persisted (Plan 3b); we deliberately do NOT surface its id here.
        # ModuleOutput.persisted_run_id is renamed to `asset_allocation_run_id` by the
        # brain, so emitting an additional_investment_runs.id would mislabel it as an
        # AA-run id. The id surfaces instead via additional_investment_run_id below.
        persisted_run_id=None,
        additional_investment_run_id=result.additional_investment_run_id,
        additional_investment_cadence=result.additional_investment_cadence,
        chart_payloads=result.chart_payloads,  # forward hook: the ainv engine does not populate this yet
    )


__all__ = ["run"]
