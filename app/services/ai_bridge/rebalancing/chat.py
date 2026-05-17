"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.common import build_detect_history_block
from app.services.ai_bridge.intent_router import classify_action
from app.services.ai_bridge.rebalancing.service import (
    build_rebal_facts_pack,
    compute_rebalancing_result,
)
from app.services.chat_core.turn_context import (
    AgentRunRecord,
    TurnContext,
    upsert_awaiting_save,
)
from app.services.ai_bridge.answer_formatter import format_with_telemetry
from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief
from app.services.ai_bridge.rebalancing.overrides import (
    _REBAL_ALLOWED_OVERRIDE_KEYS,
    with_chat_overrides,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action schema
# ---------------------------------------------------------------------------

class RebalanceAction(BaseModel):
    mode: Literal[
        "narrate",
        "educate",
        "counterfactual_explore",
        "save_last_counterfactual",
        "recompute",
        "clarify",
        "redirect",
    ]
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "For counterfactual_explore. Allowed keys: effective_tax_rate, "
            "stcg_offset_budget_inr, carryforward_st_loss_inr, "
            "carryforward_lt_loss_inr."
        ),
    )
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)


_INVALID_OVERRIDE_TEMPLATE = (
    "I can only run 'what if' scenarios on a small set of inputs from chat "
    "right now (tax rate, STCG offset budget, carry-forward losses, additional "
    "cash to deploy). Other changes — like deferring the rebalance — aren't "
    "supported yet. If you'd like a 'what if' on the supported inputs, just "
    "say so."
)


# ---------------------------------------------------------------------------
# Prompts and templates
# ---------------------------------------------------------------------------

_DETECT_REBAL_SYSTEM = """You decide how to handle a chat turn about a customer's
mutual fund rebalancing recommendation. Pick exactly one mode from the list below.

- "narrate" — the question asks about THIS customer's current rebalancing
  recommendation or its specific trades/numbers ("why are you selling X?",
  "what's the tax impact?", "explain this exit", "is this a lot of trades?").
  The answer's substantive content is the customer's specific values
  (sub_categories, ₹ amounts, tax estimates).
- "educate" — the question asks what a term or mechanism MEANS in general
  ("what is exit load?", "what's STCG vs LTCG?", "what does 'partial exit'
  mean?", "why does tax matter for rebalancing?"). The answer leads with a
  plain-English definition; the customer's data is illustration anchored at
  the end. Tie-break (narrate vs educate): if the question references the
  customer's specific values ("why am I charged exit load on fund X?"),
  prefer narrate.
- "counterfactual_explore" — ANY question expressing a constraint or
  hypothetical with at least one concrete value the customer wants to
  test. This covers BOTH "what if" curiosity ("what if my tax rate were
  20%?") AND commit-shaped requests ("save with 20% tax rate", "lock
  this in with ₹2L more"). Don't try to disambiguate verb intent —
  always emit counterfactual_explore here. The handler runs the engine
  and offers the customer a chance to save; if they confirm in the next
  turn, classify that as `save_last_counterfactual`. Must specify
  `overrides`. Allowed override keys (others → redirect):
    effective_tax_rate:        number 0-100 (% — overrides customer's tax bracket)
    stcg_offset_budget_inr:    number ≥ 0 (₹ — STCG offset budget for this run)
    carryforward_st_loss_inr:  number ≥ 0 (₹ — short-term carryforward losses)
    carryforward_lt_loss_inr:  number ≥ 0 (₹ — long-term carryforward losses)
    additional_cash_inr:       number ≥ 0 (₹ — relative, "what if I had ₹2L more to deploy?" → 200000; re-runs allocation at corpus + this, then rebalances against present holdings)
  Multiple keys are allowed in one action ("what if my tax rate were 20%
  AND I had ₹50K in carry-forward losses?"). Does NOT persist on this turn.
- "save_last_counterfactual" — the customer is committing the most
  recent counterfactual as their saved recommendation. Triggered by
  terse approvals after a counterfactual: "save", "save it", "lock it
  in", "lock in", "yes", "yeah do that", "go ahead", "make this my
  plan", "keep this", "do it". No `overrides` field needed — the system
  loads the previous turn's overrides and re-runs with persist=True.
- "recompute" — they explicitly ask to re-run with current portfolio state
  ("rebalance again", "redo this with my latest holdings"). No overrides.
- "clarify" — they signal a direction without an actionable value.
  Compose a concise clarification question in `clarification_question`.
- "redirect" — they want something we can't do from chat (lock specific funds,
  edit holdings, hypothetical "what if" with override inputs OUTSIDE the
  allow-list above — e.g. "what if I delayed by 3 months" — those aren't
  supported yet). Set `redirect_reason` to a short description.

Examples:

narrate (anchored in the customer's specific values):
- "why are you selling Mid Cap?"            → narrate
- "what's the tax impact of these sells?"   → narrate
- "is this a lot of trades?"                → narrate
- "why am I charged exit load on this?"     → narrate
                                              (references the customer's specific
                                              fund/charge — tie-break favors narrate)

educate (asking what a term or mechanism MEANS in general):
- "what's an exit load?"                    → educate
- "what's STCG vs LTCG?"                    → educate
- "why does tax matter for rebalancing?"    → educate

counterfactual_explore (hypothetical with at least one concrete value):
- "what if my tax rate were 20%?"           → counterfactual_explore,
                                              overrides={effective_tax_rate: 20}
- "what if I had ₹50K in carry-forward
  short-term losses?"                       → counterfactual_explore, overrides=
                                              {carryforward_st_loss_inr: 50000}
- "what if I had ₹2L more to deploy?"       → counterfactual_explore,
                                              overrides={additional_cash_inr: 200000}
- "save with 20% tax rate"                  → counterfactual_explore,
                                              overrides={effective_tax_rate: 20}
                                              (commit-shaped — still emit explore;
                                              the system offers a save next turn)
- "what if my tax were 20% AND I had ₹50K
  in short-term losses?"                    → counterfactual_explore, overrides=
                                              {effective_tax_rate: 20,
                                               carryforward_st_loss_inr: 50000}

save_last_counterfactual (terse approval right after a counterfactual_explore):
- "save it" / "lock it in"                  → save_last_counterfactual
- "yes, do that" / "make this my plan"      → save_last_counterfactual

recompute:
- "rebalance my portfolio"                  → recompute
- "redo with my latest holdings"            → recompute

redirect (out of scope, or override outside the allow-list):
- "what if I delayed by 3 months?"          → redirect, "delay rebalance by N months"
- "don't sell my HDFC Top 100"              → redirect, "lock specific holdings"

clarify (direction without an actionable value):
- "I want to reduce tax"                    → clarify, "Your effective tax rate
                                              is X% — would 20% feel right?"
"""

_REBAL_FORMATTER_BODY = """You are answering a customer's question about a
mutual-fund rebalancing recommendation. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  total_portfolio_inr / total_portfolio_indian — total invested corpus across all holdings
  buys_total_inr / buys_total_indian — sum of recommended buy amounts
  sells_total_inr / sells_total_indian — sum of recommended sell amounts
  tax_impact_inr / tax_impact_indian — estimated tax payable on the sells
  trade_count: int — number of distinct buy/sell trades in the recommendation

  asset_class_mix_pct: {equity, debt, others} as percentages of total
  asset_class_mix_inr: {equity, debt, others} as ₹ amounts
  asset_class_mix_indian: {equity, debt, others} pre-formatted strings

  buckets: list of one entry per (sub_category) the customer holds or trades.
    Fields per bucket:
      sub_category    — SEBI category name, e.g. "Large Cap Fund", "Liquid Fund".
                        THIS is the customer-facing label; copy verbatim.
      asset_subgroup  — internal engine grouping (e.g. "low_beta_equities").
                        DO NOT surface this to the customer; it's context only.
      current_inr / current_indian       — present holding in this sub_category
      buy_inr     / buy_indian           — amount being bought
      sell_inr    / sell_indian          — amount being sold (always non-negative)
      planned_final_inr / planned_final_indian — current + buy − sell

  warnings: list of short human-readable strings (up to 5)

  fund_actions: list of per-fund actions for the customer's specific funds
    (top 30 by exposure; if more, ``more_holdings_count`` carries the
    overflow count for "and N other smaller holdings"). Each entry:
      fund_name        — the customer-facing scheme name (e.g. "HDFC Top 100").
                         Cite this verbatim when answering fund-specific
                         questions ("why are you trimming HDFC Top 100?",
                         "what funds will I hold after this?").
      sub_category     — SEBI category for context (e.g. "Large Cap Fund").
      asset_subgroup   — internal engine grouping; do NOT surface.
      current_inr / current_indian       — present holding in this fund
      buy_inr     / buy_indian           — amount being bought into this fund
      sell_inr    / sell_indian          — amount being sold from this fund
      planned_final_inr / planned_final_indian — current + buy − sell
    When the customer asks about a specific fund or specific trades, name
    the fund(s). When showing a "what will I hold after?" view, list funds
    with planned_final > 0, biggest first. For category-level questions,
    prefer the aggregated ``buckets`` field — fund-level detail is only
    needed when the question is fund-specific.

  goal_buckets: optional list — present when the rebalancing was driven by the
    customer's goals. One entry per bucket the customer has goals in:
      bucket             — "emergency" / "short_term" / "medium_term" / "long_term"
      horizon_label      — customer-friendly label, e.g. "Long-term (> 5 yrs)".
                           Use this verbatim instead of the raw bucket key.
      goals: list of {name, horizon_months, amount_needed_inr,
                      amount_needed_indian, priority}. Priority is
                      "non_negotiable" or "negotiable" — phrase as
                      "must-meet" / "flexible" rather than the raw label.
      total_goal_amount_indian / allocated_amount_indian — pre-formatted ₹.
      planned_split_pct  — {equity, debt, others} % the AA engine targeted for
                           this bucket based on the goals' horizons. THIS is
                           why each bucket has the equity/debt mix it does.

  When goal_buckets is present and it makes the answer clearer, tie trades
  back to the bucket and its goal(s): e.g. "we're trimming equity in your
  short-term bucket because your house-down-payment goal is ~18 months away,
  so the engine targets ~30% equity / 70% debt there." Do NOT enumerate every
  bucket on every turn — only surface the bucket(s) the customer's question
  touches. If goal_buckets is absent, answer purely from the trade/asset-class
  facts as before.

ACTION_MODE tells you the situation. ACTION_MODE may also be `compute`,
which is set by the system on a fresh first-turn recommendation (it is not
produced by the classifier). Per-mode behavior:

  compute    — first-time rebalancing recommendation; introduce it shaped by
               the customer's question. Cover: the headline (trade_count, total
               trade volume from buys_total_indian / sells_total_indian, and
               tax_impact_indian if non-zero), the 1-2 biggest moves at
               sub_category level, the resulting asset_class_mix_indian, and
               any warning that meaningfully shapes the picture. Lead with the
               headline unless the customer's question is specifically about
               tax or a specific fund — then lead with that. If trade_count is
               0, skip the trade details — lead with the alignment fact (e.g.,
               "your portfolio is already aligned with your target mix") and
               briefly mention current asset_class_mix_indian. Length: 8-12
               sentences (3-5 for trade_count=0).
  narrate    — they're asking about the existing recommendation. Anchor in
               2-3 specific sub_categories / amounts directly tied to the
               question; do NOT list every bucket. Length: 4-7 sentences.
  educate    — they're asking what a term or mechanism MEANS (e.g. exit
               load, STCG/LTCG, partial exit). Lead with a one-line plain-
               English definition, then anchor it in at least one specific
               from FACTS_PACK (a sub_category, a trade, a tax/exit-load
               amount). Length: 4-7 sentences.
  recompute  — re-ran with current state. Acknowledge the re-run briefly
               and lead with what changed since the last run. Length: 6-10
               sentences.
  counterfactual_explore — hypothetical-only result. Make clear this is a
               hypothetical for comparison, not the saved recommendation;
               reference the saved recommendation as the baseline but
               don't reprint it in full. End the response with an explicit
               save offer like "Want me to save this as your active
               recommendation? Just say 'save' or 'lock it in'." Length:
               6-10 sentences (including the save offer).
  save_last_counterfactual — the customer is committing the most recent
               counterfactual as their saved recommendation. Lead with
               "Saved." then briefly state what was committed (which
               override(s) were applied) and the resulting trades / mix.
               Length: 4-6 sentences.
"""

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** or **Holdings** page and update "
    "the relevant inputs — I'll regenerate the rebalancing plan automatically."
)

_DEFAULT_CLARIFY_FALLBACK = (
    "Could you share a bit more — e.g., a specific fund, action (sell/swap), "
    "or constraint?"
)

_NO_PENDING_COUNTERFACTUAL_MESSAGE = (
    "There's no recent 'what if' to save in this conversation. "
    "If you'd like to lock in a change, tell me what you'd like "
    "different (e.g., 'what if I had ₹2L more?') and I'll show "
    "you the result first — then you can save it."
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
    goal_buckets: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Run the formatter; fall back to the precomputed templated brief on failure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_rebal_facts_pack(response, goal_buckets=goal_buckets),
        body_prompt=_REBAL_FORMATTER_BODY,
        module_name="rebalancing",
        action_mode=action_mode,
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: fallback_brief,
    )


def _rehydrate_response(payload: dict[str, Any]) -> Any:
    """Best-effort rehydration of RebalancingComputeResponse from persisted JSON.

    Returns the typed pydantic model if validation succeeds; otherwise returns
    the raw dict (the facts-pack builder uses `getattr` so a dict still works
    for missing-attr defaults).
    """
    try:
        from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]
        return RebalancingComputeResponse.model_validate(payload)
    except Exception as exc:
        logger.warning(
            "rebal_rehydration_validation_failed error_class=%s",
            type(exc).__name__,
        )
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
                                     rebalancing_recommendation_id=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="compute",
            goal_buckets=outcome.goal_buckets,
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            rebalancing_response=outcome.response,
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
                                 rebalancing_recommendation_id=None)

    if action.mode == "redirect":
        reason = action.redirect_reason or "change your trades"
        return ChatHandlerResult(text=_REDIRECT_TEMPLATE.format(reason=reason),
                                 snapshot_id=None, rebalancing_recommendation_id=None)

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(ctx, action.overrides or {})

    if action.mode == "save_last_counterfactual":
        # State-machine gate: if no counterfactual is pending save in this
        # session, the classifier guessed wrong. Return guidance instead of
        # risking a write on stale telemetry.
        if not ctx.awaiting_save:
            return ChatHandlerResult(
                text=_NO_PENDING_COUNTERFACTUAL_MESSAGE,
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        return await _save_last_counterfactual(ctx)

    # narrate / educate / recompute — all go through formatter; recompute also re-runs.
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
                                     rebalancing_recommendation_id=None)
        text = await _format_or_fallback_rebal(
            ctx=ctx, response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="recompute",
            goal_buckets=outcome.goal_buckets,
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=outcome.allocation_snapshot_id,
            rebalancing_recommendation_id=outcome.recommendation_id,
            rebalancing_response=outcome.response,
        )

    # narrate / educate — both use last_run.output_payload as the source.
    # The persisted shape is {"rebalancing_response": <model_dump>,
    # "goal_buckets": <list|None>, "correlation_ids": {...}}; see
    # rebalancing/service.py compute_rebalancing_result telemetry write.
    # ``goal_buckets`` may be absent on rows persisted before this field shipped.
    persisted_payload = last_run.output_payload or {}
    response_payload = persisted_payload.get("rebalancing_response") or {}
    persisted_goal_buckets = persisted_payload.get("goal_buckets")
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
        ctx=ctx, response=response, fallback_brief=fallback,
        action_mode=action.mode,   # "narrate" or "educate"
        goal_buckets=persisted_goal_buckets,
    )
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None)


# ---------------------------------------------------------------------------
# Override helpers (counterfactual_explore)
# ---------------------------------------------------------------------------

def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _REBAL_ALLOWED_OVERRIDE_KEYS for k in overrides.keys())


async def _counterfactual_explore(
    ctx: TurnContext, overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical.

    Writes a chat_ai_module_runs row capturing the overrides so a follow-up
    `save_last_counterfactual` turn can re-run with persist=True without
    re-classifying the original constraint. The formatter prompt instructs
    the LLM to end the response with an explicit save offer.
    """
    if not overrides or not _validate_overrides(overrides):
        return ChatHandlerResult(
            text=_INVALID_OVERRIDE_TEMPLATE,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    chat_ctx = with_chat_overrides(ctx, overrides)
    # AA-affecting overrides (currently: additional_cash_inr) require the AA
    # cache to be skipped so AA re-runs with the override applied. Tax-only
    # overrides don't change AA's output; cache is fine.
    needs_fresh_aa = "additional_cash_inr" in overrides
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        persist=False,    # counterfactual_explore — no recommendation row, no telemetry write
        force_fresh_allocation=needs_fresh_aa,
        chat_ctx=chat_ctx,
    )

    if outcome.blocking_message is not None:
        return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                 rebalancing_recommendation_id=None)
    if outcome.response is None:
        return ChatHandlerResult(
            text="I couldn't compute that hypothetical right now.",
            snapshot_id=None, rebalancing_recommendation_id=None,
        )

    # Capture overrides for a potential save_last_counterfactual follow-up,
    # and mark this session as awaiting save (cross-turn state machine).
    # Both writes are best-effort — if either fails, the customer can re-state.
    if ctx.db is not None:
        try:
            from app.services.ai_module_telemetry import record_ai_module_run
            await record_ai_module_run(
                ctx.db,
                user_id=ctx.effective_user_id,
                session_id=ctx.session_id,
                module="rebalancing",
                reason="counterfactual_overrides",
                input_payload={
                    "overrides": overrides,
                    "needs_fresh_aa": needs_fresh_aa,
                },
                emit_standard_log=False,
            )
        except Exception as exc:
            logger.warning("counterfactual_overrides_capture_failed: %s", exc)
        try:
            await upsert_awaiting_save(ctx.db, ctx.session_id, True)
        except Exception as exc:
            logger.warning("awaiting_save_upsert_failed: %s", exc)

    text = await _format_or_fallback_rebal(
        ctx=ctx, response=outcome.response,
        fallback_brief=outcome.formatted_text or "",
        action_mode="counterfactual_explore",
        goal_buckets=outcome.goal_buckets,
    )
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None)


async def _save_last_counterfactual(
    ctx: TurnContext,
) -> ChatHandlerResult:
    """Commit the most recent counterfactual_explore as the saved recommendation.

    Loads the overrides captured by ``_counterfactual_explore`` from the
    most recent chat_ai_module_runs row in this session with
    ``reason='counterfactual_overrides'``, re-runs the engine with those
    overrides AND persist=True, and returns a 'Saved.'-led response.
    """
    payload = await _load_last_counterfactual_payload(ctx)
    if payload is None:
        # Defense-in-depth: state gate said awaiting_save=True, but no
        # telemetry row found. Same guidance message as the state gate.
        return ChatHandlerResult(
            text=_NO_PENDING_COUNTERFACTUAL_MESSAGE,
            snapshot_id=None, rebalancing_recommendation_id=None,
        )

    overrides = payload.get("overrides", {})
    needs_fresh_aa = bool(payload.get("needs_fresh_aa", False))

    chat_ctx = with_chat_overrides(ctx, overrides)
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        persist=True,    # commit
        force_fresh_allocation=needs_fresh_aa,
        chat_ctx=chat_ctx,
    )

    if outcome.blocking_message is not None:
        return ChatHandlerResult(text=outcome.blocking_message, snapshot_id=None,
                                 rebalancing_recommendation_id=None)
    if outcome.response is None:
        return ChatHandlerResult(
            text="I couldn't save that recommendation right now. Please try again.",
            snapshot_id=None, rebalancing_recommendation_id=None,
        )

    # Save succeeded — clear the state-machine flag so a subsequent unrelated
    # "save it" doesn't re-commit the same recommendation.
    if ctx.db is not None:
        try:
            await upsert_awaiting_save(ctx.db, ctx.session_id, False)
        except Exception as exc:
            logger.warning("awaiting_save_reset_failed: %s", exc)

    text = await _format_or_fallback_rebal(
        ctx=ctx, response=outcome.response,
        fallback_brief=outcome.formatted_text or "",
        action_mode="save_last_counterfactual",
        goal_buckets=outcome.goal_buckets,
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
        rebalancing_response=outcome.response,
    )


async def _load_last_counterfactual_payload(
    ctx: TurnContext,
) -> Optional[dict[str, Any]]:
    """Find the overrides + flags used by the most recent counterfactual in this session."""
    if ctx.db is None or ctx.session_id is None:
        return None
    from sqlalchemy import select
    from app.models.chat_ai_module_run import ChatAiModuleRun
    stmt = (
        select(ChatAiModuleRun)
        .where(ChatAiModuleRun.session_id == ctx.session_id)
        .where(ChatAiModuleRun.module == "rebalancing")
        .where(ChatAiModuleRun.reason == "counterfactual_overrides")
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    try:
        result = await ctx.db.execute(stmt)
        row = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning("load_last_counterfactual_payload_failed: %s", exc)
        return None
    if row is None:
        return None
    payload = row.input_payload or {}
    overrides = payload.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        return None
    return payload


# ---------------------------------------------------------------------------
# LLM call — classifier for follow-up turns
# ---------------------------------------------------------------------------

_DETECT_SNAPSHOT_BUDGET = 6000


def _slim_snapshot(output_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce the persisted rebalancing snapshot to facts the classifier needs.

    Reuses ``build_rebal_facts_pack`` so the classifier sees the same curated
    view as the formatter — totals, asset-class mix, per-sub_category buckets,
    warnings — and drops verbose engine internals (per-action ISINs, raw rows,
    optimizer state).
    """
    if not output_payload:
        return {}
    payload = output_payload.get("rebalancing_response") if isinstance(
        output_payload, dict
    ) else None
    if not payload:
        return {}
    response = _rehydrate_response(payload)
    if isinstance(response, dict):
        # Validation drift — fall back to the raw response payload.
        return payload
    try:
        return build_rebal_facts_pack(response)
    except Exception as exc:
        logger.warning("rebal_slim_snapshot_failed: %s", exc)
        return {}


async def _detect_rebal_action(
    last_run: AgentRunRecord, ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction. Uses the shared classify_action."""
    slim = _slim_snapshot(last_run.output_payload)
    snapshot_json = json.dumps(slim, default=str)
    if len(snapshot_json) > _DETECT_SNAPSHOT_BUDGET:
        logger.info(
            "detect_rebal_action_snapshot_truncated original_len=%d budget=%d",
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
        f"Saved rebalancing snapshot (slim):\n{snapshot_json}"
        f"{history_section}"
    )
    return await classify_action(
        action_model=RebalanceAction,
        system_prompt=_DETECT_REBAL_SYSTEM,
        user_block=user_block,
        api_key=get_settings().get_anthropic_rebalancing_key(),
        max_tokens=300,
    )


