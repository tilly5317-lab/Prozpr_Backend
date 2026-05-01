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
from app.services.ai_bridge.answer_formatter import (
    FormatterFailure,
    format_answer,
)
from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief
from app.services.ai_bridge.rebalancing.service import (
    build_rebal_facts_pack,
)
from app.services.ai_module_telemetry import record_ai_module_run

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

_REBAL_FORMATTER_BODY = """You are answering a customer's question about a
mutual-fund rebalancing recommendation. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  total_portfolio_inr: number — total invested corpus across all holdings
  buys_total_inr: number — sum of recommended buy amounts
  sells_total_inr: number — sum of recommended sell amounts
  tax_impact_inr: number — estimated tax payable on the sells
  trade_count: int — number of distinct buy/sell trades in the recommendation
  buckets: list of {asset_subgroup, goal_target_inr, current_holding_inr,
                    suggested_final_inr, rebalance_inr}
           — per-subgroup amounts; rebalance_inr can be negative (sell) or positive (buy)
  warnings: list of short human-readable strings (up to 5)

ACTION_MODE tells you the situation:
  compute    — first-time rebalancing recommendation; introduce it shaped by
               the customer's question. If trade_count is 0, lead with that.
  narrate    — they're asking about the existing recommendation.
               Cite specific subgroups / amounts to ground the answer.
  recompute  — they asked to re-run; acknowledge and lead with what changed.

Answer the customer's question. Do not list every bucket unless asked.
"""

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** or **Holdings** page and update "
    "the relevant inputs — I'll regenerate the rebalancing plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific fund, action (sell/swap), "
    "or constraint?"
)

_NARRATE_DEGRADED_FALLBACK = (
    "I have your latest rebalancing plan but couldn't compose a tailored "
    "explanation right now. Ask me to redo the trades and I'll regenerate "
    "from your current holdings."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _format_or_fallback_rebal(
    *,
    ctx: TurnContext,
    response: Any,
    fallback_brief: str,
    action_mode: str,
) -> str:
    """Run the formatter; fall back to the precomputed templated brief on failure."""
    import time
    started = time.monotonic()
    formatter_succeeded = False
    formatter_error_class: str | None = None
    try:
        facts_pack = build_rebal_facts_pack(response)
        text = await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name="rebalancing",
            facts_pack=facts_pack,
            body_prompt=_REBAL_FORMATTER_BODY,
            history=ctx.conversation_history or [],
            profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        )
        formatter_succeeded = True
    except FormatterFailure as exc:
        formatter_error_class = type(exc).__name__
        logger.error(
            "formatter_failed mode=%s error_class=%s",
            action_mode, formatter_error_class,
        )
        text = fallback_brief
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            module="rebalancing",
            reason=f"formatter:{action_mode}",
            duration_ms=latency_ms,
            formatter_invoked=True,
            formatter_succeeded=formatter_succeeded,
            formatter_latency_ms=latency_ms,
            formatter_error_class=formatter_error_class,
            action_mode=action_mode,
            emit_standard_log=False,
        )
    return text


def _rehydrate_response(payload: dict[str, Any]) -> Any:
    """Best-effort rehydration of RebalancingComputeResponse from persisted JSON.

    Returns the typed pydantic model if validation succeeds; otherwise returns
    the raw dict (the facts-pack builder uses `getattr` so a dict still works
    for missing-attr defaults).
    """
    try:
        from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]
        return RebalancingComputeResponse.model_validate(payload)
    except Exception:
        return payload


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    last_run = ctx.last_agent_runs.get("rebalancing")

    # First turn → run engine, format compute output.
    if last_run is None:
        outcome = await compute_rebalancing_result(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            db=ctx.db,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
        )
        if outcome.blocking_message is not None:
            return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                     rebalancing_recommendation_id=None, chart=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="compute",
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
        )

    # Follow-up → classify.
    try:
        action = await _detect_rebal_action(last_run, ctx)
    except Exception as exc:
        logger.warning("detect_rebal_action failed (%s); falling back to narrate", exc)
        action = RebalanceAction(mode="narrate")

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text, snapshot_id=None,
                                 rebalancing_recommendation_id=None, chart=None)

    if action.mode == "redirect":
        reason = action.redirect_reason or "change your trades"
        return ChatHandlerResult(text=_REDIRECT_TEMPLATE.format(reason=reason),
                                 snapshot_id=None, rebalancing_recommendation_id=None,
                                 chart=None)

    # narrate or recompute — both go through formatter; recompute also re-runs.
    if action.mode == "recompute":
        outcome = await compute_rebalancing_result(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            db=ctx.db,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
        )
        if outcome.blocking_message is not None:
            return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                     rebalancing_recommendation_id=None, chart=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="recompute",
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            chart=outcome.chart.model_dump(mode="json") if outcome.chart else None,
        )

    # narrate — use last_run.output_payload as the source. The persisted shape
    # is {"rebalancing_response": <model_dump>, "correlation_ids": {...}}; see
    # rebalancing/service.py compute_rebalancing_result telemetry write.
    response_payload = (last_run.output_payload or {}).get("rebalancing_response") or {}
    response = _rehydrate_response(response_payload)
    # No persisted formatted_text — rebuild the templated fallback inline if
    # the formatter fails. If the response is dict-shaped (validation drift) or
    # build_fallback_rebal_brief raises, use the degraded text so the user never
    # sees an empty message.
    try:
        if isinstance(response, dict):
            fallback = _NARRATE_DEGRADED_FALLBACK
        else:
            fallback = build_fallback_rebal_brief(response, used_cached_allocation=False)
    except (AttributeError, TypeError, ValueError):
        fallback = _NARRATE_DEGRADED_FALLBACK
    text = await _format_or_fallback_rebal(
        ctx=ctx, response=response, fallback_brief=fallback, action_mode="narrate",
    )
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None, chart=None)


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
