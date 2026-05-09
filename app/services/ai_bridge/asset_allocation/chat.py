"""Chat handler for the asset_allocation intent.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for asset_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 7 modes
  (narrate / educate / counterfactual_explore / save_last_counterfactual /
   clarify / recompute_full / redirect), then dispatch.

Commit pattern: ``counterfactual_explore`` runs the engine with overrides and
does NOT persist; the response appends a save offer. If the customer follows
up with "save it" / "lock it in", the classifier emits
``save_last_counterfactual``, which loads the most recent counterfactual
overrides from chat_ai_module_runs and re-runs the engine with persist=True.

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
from app.services.ai_bridge.answer_formatter import format_with_telemetry
from app.services.ai_bridge.asset_allocation.service import (
    build_aa_facts_pack,
    build_fallback_brief,
    compute_allocation_result,
)
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.common import (
    build_detect_history_block,
    trace_line,
)
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
        "save_last_counterfactual",
        "clarify",
        "recompute_full",
        "redirect",
    ]
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description="For counterfactual_explore. Allowed keys: "
                    "effective_risk_score, total_corpus, "
                    "additional_cash_inr, annual_income, "
                    "monthly_household_expense, emergency_fund_needed, "
                    "tax_regime.",
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
    "additional_cash_inr":       "_chat_additional_cash_override",
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
goal-based asset allocation. Pick exactly one of seven modes.

- "narrate" — the question asks about THIS customer's plan or its data:
  "why so much X?", "is this too aggressive?", "explain my long-term mix",
  "is my allocation right?". The answer's substantive content is the
  customer's specific values (allocation %, ₹ amounts, goal mix).
- "educate" — the question asks what a term or mechanism MEANS in general:
  "what is X?", "how does Y work?", "what does Z mean for someone like me?".
  The answer leads with a plain-English definition; the customer's data, if
  used, is illustration anchored at the end. Even when the customer phrases
  the question with "for me" or "in my case", route to educate when the
  primary ask is conceptual.
- Tie-break (narrate vs educate): if a single question asks BOTH a concept
  AND a why-this-much question ("what's arbitrage and why do I have so
  much?"), prefer narrate — the concept can be woven into the personal
  explanation.
- "counterfactual_explore" — ANY question expressing a constraint or
  hypothetical with at least one concrete value the customer wants to
  test. This covers BOTH "what if" curiosity ("what if my risk were 7?")
  AND commit-shaped requests ("lock in risk 7", "save this with ₹1
  crore"). Don't try to disambiguate verb intent — always emit
  counterfactual_explore here. The handler runs the engine and offers
  the customer a chance to save; if they confirm in the next turn,
  classify that as `save_last_counterfactual`. Must specify `overrides`.
  Multiple keys allowed in one action ("what if risk were 7 AND corpus
  were ₹1 crore" → both keys). Does NOT persist on this turn.
- "save_last_counterfactual" — the customer is committing the most
  recent counterfactual as their saved plan. Triggered by terse
  approvals after a counterfactual: "save", "save it", "lock it in",
  "lock in", "yes", "yeah do that", "go ahead", "make this my plan",
  "keep this", "do it". No `overrides` field needed — the system loads
  the previous turn's overrides and re-runs with persist=True. Only
  emit this mode when the IMMEDIATELY PRECEDING turn was a
  counterfactual_explore; if there's no recent counterfactual in the
  conversation history, this is misclassified — prefer narrate or
  redirect.
- "clarify" — the customer signals a direction but does not give a usable
  value ("I can take more risk", "less debt please", "be more conservative").
  Compose a concrete clarification question in `clarification_question`,
  anchored to current values from the snapshot AND moved in the direction
  the customer signaled (higher for "more risk" / "more aggressive";
  lower for "more conservative" / "less risk"). E.g., if current risk is
  5.5 and the customer said "more risk", ask "Your current risk is 5.5 —
  would 7 feel right?". Do NOT clarify when the customer already gave a
  number or boolean — go straight to the relevant mode.
- "recompute_full" — explicit ask to re-run the plan with currently saved
  inputs ("redo my plan", "rerun", "from scratch"). No overrides. This
  refreshes the plan with current state and persists; no save offer.
- "redirect" — the customer wants something the AA chat can't do from
  here. Set `redirect_reason` to a short description. Use this for:
    • adding/editing goals or profile fields
    • off-topic / out-of-scope (other asset classes, news, politics, etc.)
    • inputs we can't override (anything outside the allow-list below)
  Note: fund-name swaps and specific fund picks ("switch from X to Y", "which large-cap fund should I pick?") should be classified as `rebalancing` upstream and should not normally reach this classifier; if such a question DOES slip through, redirect with reason "fund-level question — please ask explicitly to rebalance".

ALLOWED override keys and ranges (overrides outside this list → redirect):
  effective_risk_score:       number 1–10
  total_corpus:               number ≥ 0 (₹ — absolute corpus, replaces baseline)
  additional_cash_inr:        number ≥ 0 (₹ — relative, adds to current corpus; "what if I had ₹2L more?" → 200000)
  annual_income:              number ≥ 0 (₹)
  monthly_household_expense:  number ≥ 0 (₹)
  emergency_fund_needed:      true | false
  tax_regime:                 "old" | "new"

If the customer's value is out-of-range (e.g., "risk 15"), still emit
counterfactual_explore with the value as given — the engine validates and
clamps. Do NOT silently drop or rewrite the value.

Examples:
- "what if my risk were 7?"            → counterfactual_explore,
                                         overrides={effective_risk_score: 7}
- "what if risk is 7 and I had 1cr?"   → counterfactual_explore, overrides=
                                         {effective_risk_score: 7,
                                          total_corpus: 10000000}
- "lock in risk 7"                     → counterfactual_explore,
                                         overrides={effective_risk_score: 7}
                                         (the handler will offer to save)
- "save this with ₹1 crore corpus"     → counterfactual_explore,
                                         overrides={total_corpus: 10000000}
- "save it" / "lock it in" / "yes"     → save_last_counterfactual
  (only when the previous turn was a counterfactual_explore — the system
  loads the previous overrides and persists)
- "make this my plan"                  → save_last_counterfactual
- "I can take more risk"               → clarify, "Your current risk is 5.5
                                         — would 7 feel right?"
- "I want to be more conservative"     → clarify, "Your current risk is 5.5
                                         — would 4 feel right?"
- "redo my plan from scratch"          → recompute_full
- "why is debt so high?"               → narrate
- "what is an arbitrage fund?"         → educate
- "add a new goal"                     → redirect, "add or edit a goal"
- "tell me about Bitcoin"              → redirect, "discuss off-topic asset"
"""

_AA_FORMATTER_BODY = """You are answering a customer's question about their
goal-based asset allocation plan. The shared house-style rules above apply.

FACTS_PACK shape (treat fields not present as unknown):

  risk_score: number — customer's effective risk score (1-10)
  age: int
  total_corpus_inr: number — total invested corpus, market value in ₹
  total_corpus_indian: string — same value pre-formatted in Indian notation
  asset_class_mix_pct: {equity, debt, others} as percentages of total
  asset_class_mix_inr: {equity, debt, others} as ₹ amounts
  asset_class_mix_indian: {equity, debt, others} pre-formatted strings
  by_horizon: list of {horizon: emergency|short_term|medium_term|long_term,
              amount_inr, amount_indian, mix_pct: {equity, debt, others}}
  goals: list of {name, amount_needed_inr, amount_needed_indian,
                  horizon_months, bucket, rationale}
  future_investments: list of {horizon, funding_gap_inr,
                                funding_gap_indian, purpose}

Field semantics — read carefully:
- amount_needed_inr is the goal's **present value in TODAY's rupees**, NOT the
  inflation-adjusted amount the customer will actually need at the goal's
  target date. If the customer asks "how much will I need at retirement?",
  say you can show today's-rupees figure but the future-date amount depends
  on inflation; don't pretend amount_needed_inr is the future-date number.
- total_corpus_inr is **market value today**, not invested cost.
- funding_gap_inr is a **lump-sum gap in TODAY's rupees** between this
  bucket's present-value goal total and the corpus available right now.
  It is NOT a monthly SIP, NOT inflation-projected, NOT what the customer
  needs to invest each month. NEVER describe this number with the words
  "monthly", "every month", "per year", "SIP amount", or "₹X / month".
  Frame it as "the gap your future investments will close over the years
  ahead" or similar — not as a recurring contribution.
- horizon_months is months from today to the goal's target date.
- Numbers from different fields may not reconcile to the rupee due to
  rounding (e.g., asset_class_mix_inr may not sum exactly to
  total_corpus_inr). Do NOT add fields together to compute new totals.
  Quote what's there; if a derived number is needed, say "approximately".

Plain-language translation for any engine jargon:
- low_beta_equities       → "stable large-cap equity"
- medium_beta_equities    → "balanced equity (flexi/multi-cap)"
- high_beta_equities      → "growth equity (mid/small-cap, sectoral)"
- value_equities          → "value-style equity"
- tax_efficient_equities  → "ELSS / tax-saving equity"
- multi_asset             → "multi-asset (equity + debt + gold blend)"
- short_debt              → "short-duration debt (ultra-short / low-duration)"
- debt_subgroup           → "debt"
- arbitrage / arbitrage_plus_income → "arbitrage (debt-like, equity-taxed)"
- gold_commodities        → "gold and commodities"
- emergency / short_term / medium_term / long_term → spell out as
  "emergency reserve", "short-term goals", "medium-term goals",
  "long-term goals" respectively.

ACTION_MODE tells you the situation. ACTION_MODE may also be `compute`,
which is set by the system on a fresh first-turn plan (it is not produced
by the classifier). Per-mode behavior:

  compute                  — first-time view of a fresh plan; introduce it
                             in customer-friendly terms shaped by their
                             question. Length: 8-12 sentences. Cover the
                             headline mix, the buckets that matter, and
                             1-2 specifics tied to the question.
  narrate                  — they're asking about the existing plan. Anchor
                             the answer in at most 2-3 numbers from
                             FACTS_PACK directly tied to the question. Do
                             NOT list every bucket or restate the full
                             plan. Length: 4-7 sentences.
  educate                  — they're asking what something means. Lead with
                             a one-line plain-English definition, then
                             anchor it in at least one number from
                             FACTS_PACK that's specific to this customer.
                             Length: 4-7 sentences.
  recompute_full           — re-ran with current saved inputs. Acknowledge
                             the re-run briefly and highlight what's
                             noteworthy. Length: 6-10 sentences.
  counterfactual_explore   — hypothetical-only result. Make clear this is
                             a hypothetical for comparison, not the saved
                             plan; reference the saved plan as the
                             baseline but don't reprint it in full. End
                             the response with an explicit save offer like
                             "Want me to save this as your active plan?
                             Just say 'save' or 'lock it in'." Length:
                             6-10 sentences (including the save offer).
  save_last_counterfactual — the customer is committing the most recent
                             counterfactual as their saved plan. Lead with
                             "Saved." then briefly state what was committed
                             (which override(s) were applied) and the
                             resulting mix. Length: 4-6 sentences.
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

    if action.mode == "save_last_counterfactual":
        return await _save_last_counterfactual(ctx)

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text)

    if action.mode == "recompute_full":
        return await _recompute_full(ctx)

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
    """Run engine with overrides, do NOT persist, narrate as hypothetical.

    Writes a chat_ai_module_runs row capturing the overrides so a follow-up
    `save_last_counterfactual` turn can re-run with persist=True without
    re-classifying the original constraint. The formatter prompt instructs
    the LLM to end the response with an explicit save offer.
    """
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

    # Capture overrides for a potential save_last_counterfactual follow-up.
    # Best-effort — if the write fails, the customer can still re-state the
    # constraint to save.
    if ctx.db is not None:
        try:
            from app.services.ai_module_telemetry import record_ai_module_run
            await record_ai_module_run(
                ctx.db,
                user_id=ctx.effective_user_id,
                session_id=ctx.session_id,
                module="asset_allocation",
                reason="counterfactual_overrides",
                input_payload={"overrides": overrides},
                emit_standard_log=False,
            )
        except Exception as exc:
            logger.warning("counterfactual_overrides_capture_failed: %s", exc)

    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="counterfactual_explore", spine_mode="counterfactual",
    )
    return ChatHandlerResult(text=text)


async def _recompute_full(ctx: TurnContext) -> ChatHandlerResult:
    """Same as first-turn but explicitly user-requested re-run."""
    return await _first_turn_run_engine(ctx)


async def _save_last_counterfactual(
    ctx: TurnContext,
) -> ChatHandlerResult:
    """Commit the most recent counterfactual_explore as the saved plan.

    Loads the overrides captured by ``_counterfactual_explore`` from the
    most recent chat_ai_module_runs row in this session with
    ``reason='counterfactual_overrides'``, re-runs the engine with those
    overrides AND persist=True, and returns a 'Saved.'-led response.
    """
    overrides = await _load_last_counterfactual_overrides(ctx)
    if overrides is None:
        return ChatHandlerResult(
            text=(
                "There's no recent 'what if' to save in this conversation. "
                "If you'd like to lock in a change, tell me what you'd like "
                "different (e.g., 'what if my risk were 7?') and I'll show "
                "you the result first — then you can save it."
            )
        )

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
            text="I couldn't save that plan right now. Please try again."
        )
    text = await _format_or_fallback(
        ctx=ctx, output=outcome.result,
        action_mode="save_last_counterfactual", spine_mode="full",
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


async def _load_last_counterfactual_overrides(
    ctx: TurnContext,
) -> Optional[dict[str, Any]]:
    """Find the overrides used by the most recent counterfactual in this session.

    Returns None when no counterfactual_overrides row exists in this session.
    """
    if ctx.db is None or ctx.session_id is None:
        return None
    from sqlalchemy import select
    from app.models.chat_ai_module_run import ChatAiModuleRun
    stmt = (
        select(ChatAiModuleRun)
        .where(ChatAiModuleRun.session_id == ctx.session_id)
        .where(ChatAiModuleRun.module == "asset_allocation")
        .where(ChatAiModuleRun.reason == "counterfactual_overrides")
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    try:
        result = await ctx.db.execute(stmt)
        row = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning("load_last_counterfactual_overrides_failed: %s", exc)
        return None
    if row is None:
        return None
    payload = row.input_payload or {}
    overrides = payload.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        return None
    return overrides


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

_DETECT_SNAPSHOT_BUDGET = 6000


def _slim_snapshot(output_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce the persisted allocation snapshot to the fields a classifier
    needs (current saved values + goals + buckets at a glance). Drops
    heavy narrative tables that aren't useful for picking a chat mode."""
    if not output_payload:
        return {}
    alloc = (output_payload.get("allocation_result") or {}) if isinstance(
        output_payload, dict
    ) else {}
    if not alloc:
        return {}

    # Bucket allocations: keep only what classification needs.
    slim_buckets = []
    for b in alloc.get("bucket_allocations", []) or []:
        slim_buckets.append({
            "bucket": b.get("bucket"),
            "total_goal_amount": b.get("total_goal_amount"),
            "allocated_amount": b.get("allocated_amount"),
            "goals": [
                {
                    "name": g.get("goal_name"),
                    "amount_needed_inr": g.get("amount_needed"),
                    "horizon_months": g.get("time_to_goal_months"),
                }
                for g in (b.get("goals") or [])
            ],
            "has_funding_gap": b.get("future_investment") is not None,
        })

    # Top-level percentages from asset_class_breakdown.actual (drop per-bucket
    # detail and planned-vs-actual splits — too heavy for the classifier).
    acb = alloc.get("asset_class_breakdown") or {}
    actual = acb.get("actual") or {}
    mix_pct = {
        "equity": actual.get("equity_total_pct"),
        "debt": actual.get("debt_total_pct"),
        "others": actual.get("others_total_pct"),
    }

    return {
        "client_summary": alloc.get("client_summary"),
        "total_corpus_inr": alloc.get("grand_total"),
        "asset_class_mix_pct": mix_pct,
        "buckets": slim_buckets,
    }


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

    slim = _slim_snapshot(last_alloc.output_payload)
    snapshot_json = json.dumps(slim, default=str)
    if len(snapshot_json) > _DETECT_SNAPSHOT_BUDGET:
        logger.info(
            "detect_action_snapshot_truncated original_len=%d budget=%d",
            len(snapshot_json), _DETECT_SNAPSHOT_BUDGET,
        )
        snapshot_json = snapshot_json[:_DETECT_SNAPSHOT_BUDGET]

    history_block = build_detect_history_block(ctx.conversation_history)
    history_section = (
        f"\n\nRecent conversation (oldest → newest):\n{history_block}"
        if history_block else ""
    )
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Saved plan snapshot (slim):\n{snapshot_json}"
        f"{history_section}"
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
        "occupation": getattr(user, "occupation", None),
        "family_status": getattr(user, "family_status", None),
        "currency": getattr(user, "currency", None),
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
    """Run the formatter; fall back to the templated brief on failure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_aa_facts_pack(output),
        body_prompt=_AA_FORMATTER_BODY,
        module_name="asset_allocation",
        action_mode=action_mode,
        profile=_profile_dict(ctx),
        build_fallback=lambda: build_fallback_brief(output, spine_mode),
    )


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
