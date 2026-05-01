"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action schema
# ---------------------------------------------------------------------------

class RebalanceAction(BaseModel):
    mode: Literal["narrate", "recompute", "clarify", "redirect"]
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Prompts and templates
# ---------------------------------------------------------------------------

_DETECT_REBAL_SYSTEM = """You decide how to handle a chat turn about a customer's
mutual fund rebalancing recommendation. Pick exactly one of four modes:

- "narrate" — they're asking about the existing recommendation
  ("why are you selling X?", "what's the tax impact?").
- "recompute" — they explicitly ask to re-run with current portfolio state
  ("rebalance again", "redo this with my latest holdings").
- "clarify" — they signal a direction without an actionable value.
  Compose a concise clarification question in `clarification_question`.
- "redirect" — they want something we can't do from chat (lock specific funds,
  change tax preferences, edit holdings). Set `redirect_reason` to a short
  description.
"""

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** or **Holdings** page and update "
    "the relevant inputs — I'll regenerate the rebalancing plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific fund, action (sell/swap), "
    "or constraint?"
)


# ---------------------------------------------------------------------------
# Public handler — Task 13 fills this in. For now, keep the existing behavior.
# ---------------------------------------------------------------------------

@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the rebalancing pipeline for the current turn and forward the result."""
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
    )
    if outcome.blocking_message is not None:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
            chart=None,
        )
    return ChatHandlerResult(
        text=outcome.formatted_text or "",
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
        chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
    )


# ---------------------------------------------------------------------------
# LLM call — classifier for follow-up turns
# ---------------------------------------------------------------------------

async def _detect_rebal_action(
    last_run: AgentRunRecord, ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=300,
    ).with_structured_output(RebalanceAction)
    snapshot = json.dumps(last_run.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent rebalancing snapshot (truncated):\n{snapshot}"
    )
    return await _ainvoke(llm, _DETECT_REBAL_SYSTEM, user_block)


async def _ainvoke(llm: Any, system_text: str, user_text: str) -> Any:
    """Structured-output invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
