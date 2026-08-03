"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.chat_dispatcher import (
    ChatHandlerResult,
    consume_speculative_detect,
    register,
    register_speculative_detector,
)
from app.domains.ai_engine.common import build_detect_history_block
from app.domains.ai_engine.classifier_llm import classify_action
from app.domains.rebalancing.services.rebal_engine.service import (
    TAILORABLE_BLOCKERS,
    build_rebal_facts_pack,
    compute_rebalancing_result,
)
from app.domains.ai_engine.turn_context import (
    AgentRunRecord,
    TurnContext,
)
from app.domains.ai_engine.answer_formatter import (
    ActionMode,
    format_relay_or_canned,
    format_with_telemetry,
)
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
        "compute",
        "clarify",
        "redirect",
        "consolidate",
    ]
    overrides: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "For counterfactual_explore. Allowed keys: effective_tax_rate, "
            "stcg_offset_budget_inr, carryforward_st_loss_inr, "
            "carryforward_lt_loss_inr, additional_cash_inr."
        ),
    )
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)
    target_fund_count: Optional[int] = Field(
        default=None,
        description=(
            "For consolidate. Max number of NEW-BUY funds the customer wants "
            "(not the portfolio's total fund count)."
        ),
    )
    allowed_categories: Optional[list[str]] = Field(
        default=None,
        description=(
            "For consolidate. The customer's fund-category words verbatim "
            "(e.g. ['large cap', 'mid cap']) to restrict new buys to. Extract "
            "the words as-is; do not guess internal keys."
        ),
    )


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
- "compute" — they explicitly ask to re-run with current portfolio state
  ("rebalance again", "redo this with my latest holdings"). No overrides.
- "clarify" — they signal a direction without an actionable value.
  Compose a concise clarification question in `clarification_question`.
- "consolidate" — they want FEWER new-buy funds, or the new money restricted
  to specific fund categories. This reshapes only the BUY side of the plan
  (sells and tax are untouched). Two optional fields:
    target_fund_count: int — "reduce my trades", "fewer funds", "keep it to 5
      funds" → the max number of NEW-BUY funds. If they say a number, set it.
      "exactly N funds for my whole portfolio" is NOT supported, but still emit
      consolidate with target_fund_count=N (the handler adds an honesty note).
    allowed_categories: list[str] — "only largecap", "just mid and small cap",
      "put it all in gold" → the customer's category WORDS verbatim
      (["large cap"], ["mid cap", "small cap"]). Extract the words as-is; never
      invent internal keys.
  If they clearly want fewer funds but give NO count and NO categories ("reduce
  my trades, too many"), emit consolidate with BOTH fields null — the handler
  asks once. HISTORY-FILL: if the recent conversation shows we JUST asked how
  many funds (or which categories) and this message supplies it ("5 funds",
  "largecap only"), emit consolidate with that field filled — do NOT re-ask.
  NOTE: "show me the full/original plan again" / "undo that" is NOT consolidate
  — it's narrate (there is no stored constraint to remove).
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

consolidate (fewer buys, or buys restricted to categories):
- "reduce my trades, it's too many"         → consolidate, both fields null
                                              (no count given — handler asks once)
- "consolidate into 5 funds"                → consolidate, target_fund_count=5
- "only invest in largecap and midcap"      → consolidate,
                                              allowed_categories=["large cap","mid cap"]
- (we just asked "how many funds?") "5"     → consolidate, target_fund_count=5
                                              (history-fill — do not re-ask)

compute:
- "rebalance my portfolio"                  → compute
- "redo with my latest holdings"            → compute

redirect (out of scope, or override outside the allow-list):
- "what if I delayed by 3 months?"          → redirect, "delay rebalance by N months"
- "don't sell my HDFC Top 100"              → redirect, "lock specific holdings"

clarify (direction without an actionable value):
- "I want to reduce tax"                    → clarify, "Your effective tax rate
                                              is X% — would 20% feel right?"
"""

_REBAL_FORMATTER_BODY = """You are answering a customer's question about a
mutual-fund rebalancing recommendation. The shared house-style rules above apply.

The CUSTOMER_RECORD has this shape (treat fields not present as unknown):

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

  constraint_impact: optional — present ONLY on a consolidate turn (the
    customer asked for fewer funds or category-restricted buys). Fields:
      target_mix_pct: {equity, debt, others} — the ideal target mix.
      unconstrained_mix_pct / constrained_mix_pct — the plan's asset-class mix
        before vs after applying their constraint.
      largest_deviations: [[label, delta_pct], ...] — biggest asset-class moves
        vs target (may all be ~0 for an intra-equity ask like "only largecap").
      buy_mix_by_category: {unconstrained: {cat: pct}, constrained: {cat: pct}}
        — how the NEW-BUY money splits across fund categories, before vs after.
        This ALWAYS moves when the constraint bites; use it when the
        asset-class deltas are flat.
      risk_profile: the customer's risk profile label (may be null).

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

ACTION_MODE tells you the situation. Per-mode behavior:

  compute    — a rebalancing recommendation we just produced; introduce it shaped by
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
               When CUSTOMER_RECORD carries `is_rerun: true` the customer asked
               us to run it again and has seen a plan before: open by
               acknowledging the re-run and lead with what changed since the
               last run instead of introducing the plan. Length: 6-10 sentences.
  narrate    — they're asking about the existing recommendation. Anchor in
               2-3 specific sub_categories / amounts directly tied to the
               question; do NOT list every bucket. Length: 4-7 sentences.
  educate    — they're asking what a term or mechanism MEANS (e.g. exit
               load, STCG/LTCG, partial exit). Lead with a one-line plain-
               English definition, then anchor it in at least one specific
               from CUSTOMER_RECORD (a sub_category, a trade, a tax/exit-load
               amount). Length: 4-7 sentences.
  counterfactual_explore — hypothetical-only result. Make clear this is a
               hypothetical for comparison, not the saved recommendation;
               reference the saved recommendation as the baseline but
               don't reprint it in full. Length: 6-10 sentences.
  consolidate — the customer asked for fewer new-buy funds and/or buys
               restricted to categories; CUSTOMER_RECORD reflects the reshaped
               buys and carries constraint_impact. FIRST confirm you did
               exactly what they asked (name the funds now being bought and
               the count). THEN add ONE grounded caution, picking the lens
               that actually moved: if largest_deviations shows a real
               asset-class shift, cite it ("this pushes you ~X% further from
               your target debt allocation"); if the asset-class deltas are
               flat, use buy_mix_by_category ("your new money now goes 100%
               into large-cap, where the plan spread it across N categories").
               Never refuse; never invent a percentage not in the block.
               Remind them the sells and tax are unchanged from the plan if
               relevant. This is a chat-only view — their saved plan is
               unchanged. Length: 6-10 sentences.
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

_CONSOLIDATE_CLARIFY = (
    "Happy to consolidate. How many funds would you like the new investments "
    "spread across — for example, up to 3 or up to 5?"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _format_or_fallback_rebal(
    *,
    ctx: TurnContext,
    response: Any,
    fallback_brief: str,
    action_mode: ActionMode,
    goal_buckets: Optional[list[dict[str, Any]]] = None,
    constraint_impact: Optional[dict[str, Any]] = None,
    is_rerun: bool = False,
) -> str:
    """Run the formatter; fall back to the precomputed templated brief on failure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_rebal_facts_pack(
            response,
            goal_buckets=goal_buckets,
            constraint_impact=constraint_impact,
            is_rerun=is_rerun,
        ),
        body_prompt=_REBAL_FORMATTER_BODY,
        module_name="rebalancing",
        action_mode=action_mode,
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: fallback_brief,
    )


async def _blocking_text(ctx: TurnContext, blocking_message: str) -> str:
    """Tailor data-gap gates (missing DOB / no holdings) through the formatter;
    keep transient/data-quality error blockers verbatim."""
    if blocking_message in TAILORABLE_BLOCKERS:
        return await format_relay_or_canned(
            ctx=ctx,
            module_name="rebalancing",
            message=blocking_message,
            action_mode="gather",
        )
    return blocking_message


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


@register_speculative_detector("rebalancing")
async def _speculative_detect(ctx: TurnContext) -> RebalanceAction | None:
    """Follow-up action detect, started by the brain concurrently with the
    intent classifier (audit F4). Pure read — same call `handle` would make."""
    last_run = ctx.last_agent_runs.get("rebalancing")
    if last_run is None:
        return None
    return await _detect_rebal_action(last_run, ctx)


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
            text = await _blocking_text(ctx, outcome.blocking_message)
            return ChatHandlerResult(
                text=text,
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

    # Follow-up → classify. Prefer the brain's speculative detect result;
    # serial detect is the fallback when speculation didn't run or failed.
    try:
        action = await consume_speculative_detect(ctx)
        if action is None:
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
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="rebalancing",
            message=_REDIRECT_TEMPLATE.format(reason=reason),
        )
        return ChatHandlerResult(
            text=text,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(ctx, action.overrides or {})

    if action.mode == "consolidate":
        return await _consolidate(ctx, action)

    # narrate / educate / compute — all go through formatter; compute also re-runs.
    if action.mode == "compute":
        outcome = await compute_rebalancing_result(
            user=ctx.user_ctx,
            user_question=ctx.user_question,
            db=ctx.db,
            acting_user_id=ctx.effective_user_id,
            chat_session_id=ctx.session_id,
        )
        if outcome.blocking_message is not None:
            text = await _blocking_text(ctx, outcome.blocking_message)
            return ChatHandlerResult(
                text=text,
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        text = await _format_or_fallback_rebal(
            ctx=ctx,
            response=outcome.response,
            fallback_brief=outcome.formatted_text or "",
            action_mode="compute",
            goal_buckets=outcome.goal_buckets,
            is_rerun=True,
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
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="rebalancing",
            message=_INVALID_OVERRIDE_TEMPLATE,
        )
        return ChatHandlerResult(
            text=text,
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
        text = await _blocking_text(ctx, outcome.blocking_message)
        return ChatHandlerResult(
            text=text,
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
# Consolidation helper (buy-side reshape — stateless, chat-only, no persist)
# ---------------------------------------------------------------------------


async def _consolidate(ctx: TurnContext, action: RebalanceAction) -> ChatHandlerResult:
    """Reshape the BUY side of a freshly-computed plan per the customer's
    constraint (fewer funds / only certain categories) and narrate it. Runs the
    engine ONCE with persist=False; nothing is stored. Sells and tax untouched.
    """
    from Rebalancing.consolidation import (  # type: ignore[import-not-found]
        ConsolidationConstraints,
        constraints_active,
        reshape_response,
    )
    from app.domains.mutual_funds.services.category_resolver import resolve_categories
    from app.domains.rebalancing.services.rebal_engine.constraint_impact import (
        build_constraint_impact,
    )

    # Canonicalise the customer's category words via the shared resolver.
    allowed: tuple[str, ...] | None = None
    if action.allowed_categories:
        resolved, unresolved = resolve_categories(action.allowed_categories)
        if unresolved and not resolved:
            return ChatHandlerResult(
                text=(
                    f"I couldn't match {', '.join(unresolved)} to a fund category "
                    "we invest in. Did you mean large-cap, mid-cap, small-cap, "
                    "hybrid, gold, or overseas equity?"
                ),
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        allowed = tuple(resolved) if resolved else None

    constraints = ConsolidationConstraints(
        target_fund_count=action.target_fund_count,
        allowed_categories=allowed,
    )

    # Incomplete ask ("fewer funds", no count/category) → ask ONCE, stateless.
    if not constraints_active(constraints):
        return ChatHandlerResult(
            text=_CONSOLIDATE_CLARIFY,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    # Run the engine ONCE, compute-only (no RebalancingRun written).
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        persist=False,
        chat_ctx=ctx,
    )
    if outcome.blocking_message is not None:
        text = await _blocking_text(ctx, outcome.blocking_message)
        return ChatHandlerResult(
            text=text, snapshot_id=None, rebalancing_recommendation_id=None
        )
    if outcome.response is None:
        return ChatHandlerResult(
            text=_NARRATE_DEGRADED_FALLBACK,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    reshaped, err = reshape_response(outcome.response, constraints)
    if err == "category_not_in_plan":
        cats = ", ".join(action.allowed_categories or [])
        return ChatHandlerResult(
            text=(
                f"Your current plan doesn't buy into {cats}, so there's nothing "
                "there to redirect the new money into. Want to see the plan as it "
                "stands, or pick a different category?"
            ),
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    impact = build_constraint_impact(
        outcome.response,
        reshaped,
        risk_profile=getattr(ctx.user_ctx, "risk_profile", None),
    )
    # Fallback brief must reflect the RESHAPED plan, not the original — else a
    # formatter failure would show the un-consolidated trades (grounding bug).
    try:
        consolidated_brief = build_fallback_rebal_brief(
            reshaped, used_cached_allocation=False
        )
    except (AttributeError, TypeError, ValueError):
        consolidated_brief = _NARRATE_DEGRADED_FALLBACK
    text = await _format_or_fallback_rebal(
        ctx=ctx,
        response=reshaped,
        fallback_brief=consolidated_brief,
        action_mode="consolidate",
        goal_buckets=outcome.goal_buckets,
        constraint_impact=impact,
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


def _classifier_digest(facts: dict[str, Any]) -> dict[str, Any]:
    """Reduce the curated facts pack to the few signals the mode classifier needs.

    The classifier only routes the customer's QUESTION into one of six modes; it
    never reads ₹ amounts (the answer is built by a separate build_rebal_facts_pack
    call in _format_or_fallback_rebal). It needs only: a recommendation exists, and
    which sub_categories / funds it covers, so the narrate-vs-educate tie-break can
    tell a fund-specific question from a general one. Names are ~1 token each; the
    per-fund/per-bucket rupee tables that dominated (and truncated) the snapshot go.
    """
    if not facts:
        return {}
    fund_actions = facts.get("fund_actions") or []
    buckets = facts.get("buckets") or []
    return {
        "has_recommendation": bool(fund_actions or buckets),
        "trade_count": facts.get("trade_count"),
        "has_sells": any((fa.get("sell_inr") or 0) > 0 for fa in fund_actions),
        "sub_categories": list(
            dict.fromkeys(b.get("sub_category") for b in buckets if b.get("sub_category"))
        ),
        "fund_names": list(
            dict.fromkeys(fa.get("fund_name") for fa in fund_actions if fa.get("fund_name"))
        ),
    }


async def _detect_rebal_action(
    last_run: AgentRunRecord,
    ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction. Uses the shared classify_action."""
    slim = _slim_snapshot(last_run.output_payload)
    snapshot_json = json.dumps(_classifier_digest(slim), default=str)
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
