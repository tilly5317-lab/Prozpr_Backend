"""Cashflow AI module — the ONLY gateway to ``AI_Agents.cashflow_statement``.

Per the AI-module rule, nothing else in the codebase imports the cashflow
statement orchestrator — they call ``run(turn, ctx, prior)`` here.

Sequence position: the brain runs this for the ``goal_planning`` intent
(the user message describes a goal / time-horizon / counterfactual). The
underlying handler may open the ``awaiting_save`` gate on
``chat_session_state`` so the next turn is routed back here regardless of
classifier verdict.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    # Lazy imports for the @register side-effect.
    from app.domains.cashflow.services.goal_planning_engine import chat as _gp_chat  # noqa: F401
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("goal_planning", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,
        chart_payloads=result.chart_payloads,
        # awaiting_save (multi-turn save gate) lives on the underlying chat
        # session state row already — we don't echo it through side_effects.
    )


__all__ = ["run"]
