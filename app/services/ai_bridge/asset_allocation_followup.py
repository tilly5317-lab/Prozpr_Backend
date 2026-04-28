"""Read-only narration + counterfactual + redirect handler for allocation followups.

Registered against ``portfolio_optimisation`` and ``goal_planning`` intents.
The brain invokes ``handle_allocation_followup`` whenever a follow-up turn
should reason over the persisted allocation snapshot rather than re-running
the engine.

This module owns the narrate + redirect paths. Counterfactual (run engine
with overrides) lives in ``asset_allocation_followup_counterfactual.py``
(Task 10) — imported lazily from inside the handler so this module remains
importable on its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.followup_dispatcher import register
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action-detection schema (returned by the small classifier LLM call)
# ---------------------------------------------------------------------------

class FollowupAction(BaseModel):
    mode: Literal["narrate", "counterfactual", "redirect_mutation", "clarify"]
    counterfactual_overrides: Optional[dict[str, Any]] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)
    clarification_question: Optional[str] = Field(
        default=None,
        description="When mode='clarify', the question to ask the customer.",
    )


_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific risk score (1–10), "
    "fund name, or amount you'd like to consider?"
)

_DETECT_SYSTEM = """You decide how to handle a follow-up question about a
previously-shown asset allocation. Return one of four modes:

- "narrate" — the customer is asking for explanation, critique, or
  clarification of the existing plan ("is this too aggressive?",
  "why so much arbitrage?", "what does flexi-cap mean?").
- "counterfactual" — the customer is asking a hypothetical "what if"
  about a single overrideable input. The ONLY supported override in
  this iteration is `effective_risk_score` (1.0–10.0). Set
  `counterfactual_overrides = {"effective_risk_score": <value>}`. Any
  other override request must fall through to "redirect_mutation".
- "redirect_mutation" — the customer wants to change holdings, swap a
  specific fund, or update saved profile data ("swap arbitrage for
  liquid", "exclude my emergency fund"). Mutation requests need a
  specific instrument or fund name; vague direction signals go to
  `clarify` instead. Set `redirect_reason` to a short description of
  what they want. The handler will respond with a templated redirect to
  the Profile UI.
- "clarify" — the customer signals a direction but doesn't provide an
  actionable value ("I can take more risk", "I want to be more
  conservative", "less debt please"). Compose a concise clarification
  question in `clarification_question` that asks for the missing value.
  Reference the customer's current values from the snapshot when possible
  (e.g., "Your current risk score is 5.5 — would 7 feel right, or higher?").
  When the customer responds with a specific value next turn, that turn
  will route to "counterfactual" with the value applied.
"""


# ---------------------------------------------------------------------------
# Narration LLM
# ---------------------------------------------------------------------------

_NARRATE_SYSTEM = """You are Prozpr's allocation explainer. You answer
follow-up questions about a customer's already-shown goal-based allocation
plan. Use the provided snapshot to answer. Be concise (4-8 sentences),
specific (cite numbers from the snapshot), and warm. Never invent funds
or numbers. If the question can't be answered from the snapshot, say so
and offer next steps."""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

@register("portfolio_optimisation")
@register("goal_planning")
async def handle_allocation_followup(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> str:
    """Decide narrate / counterfactual / clarify / redirect, then dispatch."""
    action = await _detect_action(agent_run, ctx)
    logger.info("allocation_followup mode=%s overrides=%s",
                action.mode, action.counterfactual_overrides)

    if action.mode == "narrate":
        return await _narrate_with_llm(agent_run, ctx)

    if action.mode == "counterfactual":
        # Lazy import — the counterfactual module is added in Task 10.
        from app.services.ai_bridge.asset_allocation_followup_counterfactual import (
            run_counterfactual,
        )
        return await run_counterfactual(agent_run, ctx, action.counterfactual_overrides or {})

    if action.mode == "clarify":
        # Single-LLM-call mode: detect_action composed the question directly.
        return action.clarification_question or _DEFAULT_CLARIFY_FALLBACK

    # redirect_mutation (default branch)
    return _format_redirect(action.redirect_reason or "change your plan")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

async def _detect_action(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> FollowupAction:
    """One Haiku call returning a FollowupAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=256,
    ).with_structured_output(FollowupAction)

    snapshot = json.dumps(agent_run.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent allocation snapshot (truncated):\n{snapshot}"
    )

    return await _ainvoke(llm, _DETECT_SYSTEM, user_block)


async def _narrate_with_llm(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> str:
    """Generate the narrative reply from the persisted snapshot."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=600,
    )

    snapshot = json.dumps(agent_run.output_payload, default=str)
    profile = {
        "effective_risk_score": (agent_run.input_payload or {}).get("effective_risk_score"),
        "age": (agent_run.input_payload or {}).get("age"),
        "total_corpus": (agent_run.input_payload or {}).get("total_corpus"),
    }
    history_lines = [
        f"{m.get('role','user')}: {m.get('content','')}"
        for m in (ctx.conversation_history or [])[-6:]
    ]
    user_block = (
        f"Snapshot:\n{snapshot}\n\n"
        f"Profile (from input): {json.dumps(profile, default=str)}\n\n"
        f"Recent history:\n" + "\n".join(history_lines) + "\n\n"
        f"Customer's current question: {ctx.user_question}"
    )

    return await _ainvoke_text(llm, _NARRATE_SYSTEM, user_block)


def _format_redirect(reason: str) -> str:
    return (
        f"To {reason}, head to your **Profile** section and update the "
        "relevant inputs — I'll regenerate your plan automatically. If "
        "you want, just describe what you'd like differently and I'll "
        "re-run the allocation."
    )


# ---------------------------------------------------------------------------
# Async LangChain helpers (small wrappers so tests can patch)
# ---------------------------------------------------------------------------

async def _ainvoke(llm, system_text: str, user_text: str):
    """Structured-output invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)


async def _ainvoke_text(llm, system_text: str, user_text: str) -> str:
    """Plain-text invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    raw = await asyncio.to_thread(llm.invoke, messages)
    return raw.content if hasattr(raw, "content") else str(raw)
