"""Counterfactual ('what if?') path for allocation followups.

Allowed overrides (this iteration): ``effective_risk_score`` only.
Anything else falls through to the redirect template.

Counterfactual results are NEVER persisted as AgentRuns or recommendation
rows — they are exploratory hypotheticals, not the user's saved plan.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.services.ai_bridge.asset_allocation_service import compute_allocation_result
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)

_ALLOWED_OVERRIDE_KEYS = {"effective_risk_score"}

_REDIRECT_TEMPLATE = (
    "I can only run 'what if' on a small set of inputs from chat right now "
    "(your risk score). For other changes, head to your **Profile** section "
    "and update the relevant inputs — I'll regenerate your plan automatically."
)

_NARRATE_SYSTEM = """You explain the result of a hypothetical allocation
calculation. Make the hypothetical-ness explicit ('this is hypothetical, not
your saved plan'). Compare to the existing plan briefly. Be concise (4-7
sentences). Cite specific numbers."""


async def run_counterfactual(
    agent_run: AgentRunRecord,
    ctx: TurnContext,
    overrides: dict[str, Any],
) -> str:
    """Apply overrides to the user, run the engine, narrate. Never persists."""
    illegal = set(overrides.keys()) - _ALLOWED_OVERRIDE_KEYS
    if illegal or not overrides:
        return _REDIRECT_TEMPLATE

    if agent_run.input_payload is None:
        return _REDIRECT_TEMPLATE

    risk_override = overrides.get("effective_risk_score")
    user = ctx.user_ctx
    if risk_override is not None:
        # Builder reads this transient attribute and uses it instead of saved score.
        setattr(user, "_chat_risk_score_override", float(risk_override))

    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=None,                           # no DB writes — hypothetical
            persist_recommendation=False,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="counterfactual",
        )
    finally:
        if hasattr(user, "_chat_risk_score_override"):
            delattr(user, "_chat_risk_score_override")

    if outcome.blocking_message:
        return outcome.blocking_message
    if outcome.result is None:
        return (
            "I couldn't compute that hypothetical right now. Try again "
            "or update your inputs in your Profile."
        )

    return await _narrate_counterfactual(agent_run, ctx, outcome.result, overrides)


async def _narrate_counterfactual(
    agent_run: AgentRunRecord,
    ctx: TurnContext,
    new_result: Any,
    overrides: dict[str, Any],
) -> str:
    """Narrate the hypothetical result side-by-side with the saved plan."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=500,
    )

    saved = (agent_run.output_payload or {}).get("allocation_result", {})
    new = new_result.model_dump(mode="json") if hasattr(new_result, "model_dump") else new_result

    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Overrides applied (hypothetical): {json.dumps(overrides)}\n\n"
        f"Saved plan (do NOT change this): {json.dumps(saved, default=str)}\n\n"
        f"Hypothetical result: {json.dumps(new, default=str)}\n\n"
        "Narrate the hypothetical, comparing to the saved plan. Make it "
        "clear the hypothetical is not the user's saved plan."
    )
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": _NARRATE_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_block),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return raw.content if hasattr(raw, "content") else str(raw)
