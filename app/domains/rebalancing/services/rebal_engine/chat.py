"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.common import build_detect_history_block
from app.domains.ai_engine.classifier_llm import classify_action
from app.domains.rebalancing.services.rebal_engine.service import (
    build_rebal_facts_pack,
    compute_rebalancing_result,
)
from app.domains.ai_engine.turn_context import (
    AgentRunRecord,
    TurnContext,
)
from app.domains.ai_engine.answer_formatter import format_with_telemetry
from app.domains.rebalancing.services.rebal_engine.formatter import (
    build_fallback_rebal_brief,
)
from app.domains.rebalancing.services.rebal_engine.overrides import (
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
  always emit counterfactual_explore here. Must specify
  `overrides`. Allowed override keys (others → redirect):
    effective_tax_rate:        number 0-100 (% — overrides customer's tax bracket)
    stcg_offset_budget_inr:    number ≥ 0 (₹ — STCG offset budget for this run)
    carryforward_st_loss_inr:  number ≥ 0 (₹ — short-term carryforward losses)
    carryforward_lt_loss_inr:  number ≥ 0 (₹ — long-term carryforward losses)
    additional_cash_inr:       number ≥ 0 (₹ — relative, "what if I had ₹2L more to deploy?" → 200000; re-runs allocation at corpus + this, then rebalances against present holdings)
  Multiple keys are allowed in one action ("what if my tax rate were 20%
  AND I had ₹50K in carry-forward losses?"). Does NOT persist on this turn.
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
                                              (commit-shaped — still emit explore)
- "what if my tax were 20% AND I had ₹50K
  in short-term losses?"                    → counterfactual_explore, overrides=
                                              {effective_tax_rate: 20,
                                               carryforward_st_loss_inr: 50000}

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
               don't reprint it in full. Length: 6-10 sentences.
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
            return ChatHandlerResult(
                text=outcome.blocking_message,
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        text = await _format_or_fallback_rebal(
            ctx=ctx,
            response=outcome.response,
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
        return ChatHandlerResult(
            text=text, snapshot_id=None, rebalancing_recommendation_id=None
        )

    if action.mode == "redirect":
        reason = action.redirect_reason or "change your trades"
        return ChatHandlerResult(
            text=_REDIRECT_TEMPLATE.format(reason=reason),
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(ctx, action.overrides or {})

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
            return ChatHandlerResult(
                text=outcome.blocking_message,
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        text = await _format_or_fallback_rebal(
            ctx=ctx,
            response=outcome.response,
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
            fallback = build_fallback_rebal_brief(
                response, used_cached_allocation=False
            )
    except (AttributeError, TypeError, ValueError):
        fallback = _NARRATE_DEGRADED_FALLBACK
    text = await _format_or_fallback_rebal(
        ctx=ctx,
        response=response,
        fallback_brief=fallback,
        action_mode=action.mode,  # "narrate" or "educate"
        goal_buckets=persisted_goal_buckets,
    )
    return ChatHandlerResult(
        text=text, snapshot_id=None, rebalancing_recommendation_id=None
    )


# ---------------------------------------------------------------------------
# Override helpers (counterfactual_explore)
# ---------------------------------------------------------------------------


def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _REBAL_ALLOWED_OVERRIDE_KEYS for k in overrides.keys())


async def _counterfactual_explore(
    ctx: TurnContext,
    overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical."""
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
        persist=False,  # counterfactual_explore — no recommendation row, no telemetry write
        force_fresh_allocation=needs_fresh_aa,
        chat_ctx=chat_ctx,
    )

    if outcome.blocking_message is not None:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )
    if outcome.response is None:
        return ChatHandlerResult(
            text="I couldn't compute that hypothetical right now.",
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    text = await _format_or_fallback_rebal(
        ctx=ctx,
        response=outcome.response,
        fallback_brief=outcome.formatted_text or "",
        action_mode="counterfactual_explore",
        goal_buckets=outcome.goal_buckets,
    )
    return ChatHandlerResult(
        text=text, snapshot_id=None, rebalancing_recommendation_id=None
    )


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
    payload = (
        output_payload.get("rebalancing_response")
        if isinstance(output_payload, dict)
        else None
    )
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
    last_run: AgentRunRecord,
    ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction. Uses the shared classify_action."""
    slim = _slim_snapshot(last_run.output_payload)
    snapshot_json = json.dumps(slim, default=str)
    if len(snapshot_json) > _DETECT_SNAPSHOT_BUDGET:
        logger.info(
            "detect_rebal_action_snapshot_truncated original_len=%d budget=%d",
            len(snapshot_json),
            _DETECT_SNAPSHOT_BUDGET,
        )
        snapshot_json = snapshot_json[:_DETECT_SNAPSHOT_BUDGET]

    history_block = build_detect_history_block(ctx.conversation_history)
    history_section = (
        f"\n\nRecent conversation (oldest → newest):\n{history_block}"
        if history_block
        else ""
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
