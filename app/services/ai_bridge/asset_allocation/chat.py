"""Chat handler for the asset_allocation intent.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for asset_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 7 modes
  (narrate / educate / counterfactual_explore / clarify / recompute_full /
   recompute_with_overrides / redirect), then dispatch.

The engine wrapper compute_allocation_result lives in ``service.py`` (sibling
module) and is consumed by both this module and the standalone HTTP endpoint.

Note: this handler is registered ONLY for the asset_allocation intent.
The goal_planning intent is handled in app/services/chat_core/brain.py via a
canned redirect (no agent module exists for goal_planning yet).
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
from app.services.ai_bridge.answer_formatter import (
    FormatterFailure,
    format_answer,
)
from app.services.ai_bridge.asset_allocation.service import (
    build_aa_facts_pack,
    build_fallback_brief,
    compute_allocation_result,
)
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.common import trace_line
from app.services.ai_module_telemetry import record_ai_module_run
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action schema (structured output of _detect_action)
# ---------------------------------------------------------------------------

class ChatAction(BaseModel):
    mode: Literal[
        "narrate",
        "educate",
        "counterfactual_explore",
        "clarify",
        "recompute_full",
        "recompute_with_overrides",
        "redirect",
    ]
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description="For counterfactual_explore + recompute_with_overrides. "
                    "Allowed keys: effective_risk_score, total_corpus, "
                    "annual_income, monthly_household_expense, "
                    "emergency_fund_needed, tax_regime.",
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="When mode='clarify', the question to ask the customer.",
    )
    redirect_reason: Optional[str] = Field(
        default=None,
        description="When mode='redirect', a short description of what the user wants.",
    )


# ---------------------------------------------------------------------------
# Override allow-list
# ---------------------------------------------------------------------------

# Maps ChatAction.overrides keys → transient User attribute names that
# input_builder reads.
_OVERRIDE_KEY_TO_USER_ATTR: dict[str, str] = {
    "effective_risk_score":      "_chat_risk_score_override",
    "total_corpus":              "_chat_total_corpus_override",
    "annual_income":             "_chat_annual_income_override",
    "monthly_household_expense": "_chat_monthly_expense_override",
    "emergency_fund_needed":     "_chat_emergency_fund_needed_override",
    "tax_regime":                "_chat_tax_regime_override",
}

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** section and update the relevant "
    "inputs — I'll regenerate your plan automatically. If you'd like, just "
    "describe what you want differently and I can run a hypothetical."
)

_INVALID_OVERRIDE_TEMPLATE = (
    "I can only run 'what if' on a small set of inputs from chat right now "
    "(risk score, total corpus, income, expenses, emergency fund, tax regime). "
    "For other changes, head to your **Profile** section and I'll regenerate "
    "your plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific risk score (1–10), "
    "fund name, or amount you'd like to consider?"
)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_DETECT_SYSTEM = """You decide how to handle a chat turn about a customer's
goal-based asset allocation. Pick exactly one of seven modes:

- "narrate" — explanation, critique, or "why" questions about the existing
  plan ("is this too aggressive?", "why so much arbitrage?").
- "educate" — educational questions grounded in the snapshot ("what does
  multi-cap mean?", "how does the tax treatment work?", "what is an
  arbitrage fund?"). Distinguishable from narrate: focus is teaching a
  concept, often using the user's specific holdings as examples.
- "counterfactual_explore" — hypothetical "what if" questions where the
  user wants to see the impact of a single input change without committing
  ("what if my risk were 7?", "what if I had ₹1 crore?"). Must specify
  `overrides` with one or more allowed keys.
- "clarify" — the customer signals a direction but doesn't provide an
  actionable value ("I can take more risk", "I want to be more conservative",
  "less debt please"). Compose a concise clarification question in
  `clarification_question` that asks for the missing value. Reference current
  values from the snapshot when possible (e.g., "Your current risk score is
  5.5 — would 7 feel right?").
- "recompute_full" — the customer explicitly asks to re-run the full plan
  with their current saved inputs ("redo my plan", "rerun this", "let's do
  this again from scratch").
- "recompute_with_overrides" — the customer explicitly asks to lock in a
  new plan with one or more changes ("lock in risk 7", "update my plan with
  ₹1 crore corpus", "save this with the new tax regime"). Must specify
  `overrides`. The result PERSISTS as the new saved plan.
- "redirect" — the customer wants something we can't handle from chat
  (specific fund swaps, goal additions, profile field edits). Set
  `redirect_reason` to a short description of what they want.

**Allowed override keys:** effective_risk_score (1–10), total_corpus (≥0),
annual_income (≥0), monthly_household_expense (≥0), emergency_fund_needed
(true/false), tax_regime ("old" or "new"). Any other override request must
fall through to "redirect" with an appropriate reason.

Distinguish counterfactual_explore (no persist, exploratory) from
recompute_with_overrides (persist as new plan) by whether the customer is
exploring vs. committing. When ambiguous, prefer counterfactual_explore.
"""

_AA_FORMATTER_BODY = """You are answering a customer's question about their
goal-based asset allocation plan. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  risk_score: number — customer's effective risk score (1-10)
  age: int
  total_corpus_inr: number — total invested corpus
  asset_class_mix_pct: {equity, debt, others} as percentages of total
  asset_class_mix_inr: {equity, debt, others} as ₹ amounts
  by_horizon: list of {horizon: emergency|short_term|medium_term|long_term,
              amount_inr, mix_pct: {equity, debt, others}}
  goals: list of {name, amount_needed_inr, horizon_months, bucket, rationale}
  future_investments: list of {horizon, monthly_inr, purpose}

ACTION_MODE tells you the situation:
  compute                     — first-time view of a fresh plan; introduce it
                                in customer-friendly terms shaped by their question.
  narrate                     — they're asking about the existing plan.
                                Cite specific numbers from the facts pack to
                                ground the answer; do not list every section.
  educate                     — they're asking what something means.
                                Explain in plain language, then tie it to
                                their facts pack.
  recompute_full              — they asked to re-run the plan with current
                                inputs. Acknowledge the re-run and highlight
                                what changed.
  recompute_with_overrides    — they locked in a new plan with changes.
                                Lead with what changed and the new mix.
  counterfactual_explore      — hypothetical-only result. Open with
                                "this is hypothetical, not your saved plan",
                                then compare to the saved plan.

Answer the customer's question. Do not default to a fixed template — what they
asked dictates the structure of the response.
"""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

@register("asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Sole entry point for chat turns in this intent family."""
    last_alloc = ctx.last_agent_runs.get("asset_allocation")

    if last_alloc is None:
        # First turn (or no persisted snapshot in this session) → run engine.
        return await _first_turn_run_engine(ctx)

    # Follow-up turn → decide what to do.
    try:
        action = await _detect_action(last_alloc, ctx)
    except Exception as exc:
        logger.error(
            "detect_action_failed error_class=%s",
            type(exc).__name__,
        )
        return ChatHandlerResult(
            text=(
                "I'm having trouble understanding that right now. "
                "Could you rephrase, or ask me to redo your plan?"
            )
        )

    logger.info("asset_allocation_chat mode=%s overrides=%s",
                action.mode, action.overrides)
    trace_line(f"asset_allocation_chat mode={action.mode}")

    return await _dispatch_action(action, last_alloc, ctx)


# ---------------------------------------------------------------------------
# Mode dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_action(
    action: ChatAction, last_alloc: AgentRunRecord, ctx: TurnContext,
) -> ChatHandlerResult:
    if action.mode in ("narrate", "educate"):
        try:
            output = _rehydrate_last_alloc_output(last_alloc)
        except Exception as exc:
            logger.error(
                "rehydrate_last_alloc_output_failed mode=%s error_class=%s",
                action.mode, type(exc).__name__,
            )
            return ChatHandlerResult(
                text=(
                    "I couldn't load your last plan to answer that. "
                    "Try asking me to redo the plan and we'll work from there."
                )
            )
        text = await _format_or_fallback(
            ctx=ctx, output=output, action_mode=action.mode, spine_mode="full",
        )
        return ChatHandlerResult(text=text)

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(last_alloc, ctx, action.overrides or {})

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text)

    if action.mode == "recompute_full":
        return await _recompute_full(ctx)

    if action.mode == "recompute_with_overrides":
        return await _recompute_with_overrides(ctx, action.overrides or {})

    # redirect (default)
    reason = action.redirect_reason or "change your plan"
    return ChatHandlerResult(text=_REDIRECT_TEMPLATE.format(reason=reason))


# ---------------------------------------------------------------------------
# Per-mode handlers
# ---------------------------------------------------------------------------

async def _first_turn_run_engine(ctx: TurnContext) -> ChatHandlerResult:
    """Run the engine on a fresh session (or session with no allocation yet)."""
    outcome = await compute_allocation_result(
        ctx.user_ctx, ctx.user_question,
        db=ctx.db,
        persist_recommendation=ctx.db is not None,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="full",
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't produce an allocation right now. Please try again."
        )
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result, action_mode="compute", spine_mode="full",
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


async def _counterfactual_explore(
    last_alloc: AgentRunRecord, ctx: TurnContext, overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical."""
    if not overrides or not _validate_overrides(overrides):
        return ChatHandlerResult(text=_INVALID_OVERRIDE_TEMPLATE)

    user = ctx.user_ctx
    _apply_overrides(user, overrides)
    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=None,                          # NO writes
            persist_recommendation=False,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="counterfactual",
        )
    finally:
        _clear_overrides(user, overrides)

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't compute that hypothetical right now."
        )
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="counterfactual_explore", spine_mode="counterfactual",
    )
    return ChatHandlerResult(text=text)


async def _recompute_full(ctx: TurnContext) -> ChatHandlerResult:
    """Same as first-turn but explicitly user-requested re-run."""
    return await _first_turn_run_engine(ctx)


async def _recompute_with_overrides(
    ctx: TurnContext, overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides AND persist as the new saved plan."""
    if not overrides or not _validate_overrides(overrides):
        return ChatHandlerResult(text=_INVALID_OVERRIDE_TEMPLATE)

    user = ctx.user_ctx
    _apply_overrides(user, overrides)
    try:
        outcome = await compute_allocation_result(
            user, ctx.user_question,
            db=ctx.db,                        # persist
            persist_recommendation=ctx.db is not None,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
            spine_mode="full",
        )
    finally:
        _clear_overrides(user, overrides)

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't compute the updated plan right now."
        )
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="recompute_with_overrides", spine_mode="full",
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


# ---------------------------------------------------------------------------
# Override helpers
# ---------------------------------------------------------------------------

def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _OVERRIDE_KEY_TO_USER_ATTR for k in overrides.keys())


def _apply_overrides(user: Any, overrides: dict[str, Any]) -> None:
    for key, val in overrides.items():
        attr = _OVERRIDE_KEY_TO_USER_ATTR.get(key)
        if attr is None:
            continue
        setattr(user, attr, val)


def _clear_overrides(user: Any, overrides: dict[str, Any]) -> None:
    for key in overrides.keys():
        attr = _OVERRIDE_KEY_TO_USER_ATTR.get(key)
        if attr is None:
            continue
        try:
            delattr(user, attr)
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

async def _detect_action(
    last_alloc: AgentRunRecord, ctx: TurnContext,
) -> ChatAction:
    """One Haiku call returning a ChatAction."""
    api_key = get_settings().get_anthropic_asset_allocation_key()
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=400,
    ).with_structured_output(ChatAction)

    snapshot = json.dumps(last_alloc.output_payload, default=str)[:6000]
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Most recent allocation snapshot (truncated):\n{snapshot}"
    )
    return await _ainvoke(llm, _DETECT_SYSTEM, user_block)


# ---------------------------------------------------------------------------
# Formatter helpers
# ---------------------------------------------------------------------------

def _profile_dict(ctx: TurnContext) -> dict[str, Any]:
    """Pull the customer's profile fields the formatter cares about."""
    user = ctx.user_ctx
    return {
        "age": getattr(user, "age", None) or _years_since(getattr(user, "date_of_birth", None)),
        "first_name": getattr(user, "first_name", None),
    }


def _years_since(dob: Any) -> int | None:
    if dob is None:
        return None
    from datetime import date
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _rehydrate_last_alloc_output(last_alloc: AgentRunRecord) -> Any:
    """Parse the persisted allocation_result JSON back into a GoalAllocationOutput.

    Used on follow-up turns when we don't re-run the engine but need the typed
    output to feed `build_aa_facts_pack` and the fallback brief.
    """
    from asset_allocation_pydantic.models import GoalAllocationOutput  # type: ignore[import-not-found]
    payload = (last_alloc.output_payload or {}).get("allocation_result") or {}
    return GoalAllocationOutput.model_validate(payload)


async def _format_or_fallback(
    *,
    ctx: TurnContext,
    output: Any,
    action_mode: str,
    spine_mode: str,
) -> str:
    """Run the formatter; fall back to the templated brief on failure.

    Records a ChatAiModuleRun row with formatter timing and success/failure.
    """
    import time
    started = time.monotonic()
    formatter_succeeded = False
    formatter_error_class: str | None = None
    try:
        facts_pack = build_aa_facts_pack(output)
        text = await format_answer(
            question=ctx.user_question,
            action_mode=action_mode,
            module_name="asset_allocation",
            facts_pack=facts_pack,
            body_prompt=_AA_FORMATTER_BODY,
            history=ctx.conversation_history or [],
            profile=_profile_dict(ctx),
        )
        formatter_succeeded = True
    except FormatterFailure as exc:
        formatter_error_class = type(exc).__name__
        logger.error(
            "formatter_failed mode=%s error_class=%s",
            action_mode, formatter_error_class,
        )
        text = build_fallback_brief(output, spine_mode)
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            module="asset_allocation",
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


# ---------------------------------------------------------------------------
# Async LangChain helpers (kept tiny so tests can patch easily)
# ---------------------------------------------------------------------------

async def _ainvoke(llm: Any, system_text: str, user_text: str) -> Any:
    """Structured-output invocation."""
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]),
        HumanMessage(content=user_text),
    ]
    return await asyncio.to_thread(llm.invoke, messages)
