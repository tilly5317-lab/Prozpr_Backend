"""Chat handler for the asset_allocation intent.

Single entry point for the entire chat lifecycle of allocation conversations:
- First turn (no AgentRun for asset_allocation in session) → run engine,
  persist, return chat brief
- Subsequent turns → call _detect_action LLM to pick one of 6 modes
  (narrate / educate / counterfactual_explore / clarify / recompute_full /
   redirect), then dispatch.

``counterfactual_explore`` runs the engine with overrides and does NOT
persist; it narrates the result as a hypothetical for comparison against the
saved plan.

The engine wrapper compute_allocation_result lives in ``service.py`` (sibling
module) and is consumed by both this module and the standalone HTTP endpoint.

Note: this handler is registered ONLY for the asset_allocation intent.
The goal_planning intent is handled in app/services/chat_core/brain.py via a
canned redirect (no agent module exists for goal_planning yet).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.answer_formatter import (
    ActionMode,
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.classifier_llm import classify_action
from app.domains.profile.services import profile_finance as pf
from app.domains.asset_allocation.services.aa_engine.service import (
    build_aa_facts_pack,
    compute_current_asset_class_mix,
    build_fallback_brief,
    compute_allocation_result,
)
from app.domains.ai_engine.chat_dispatcher import (
    ChatHandlerResult,
    consume_speculative_detect,
    register,
    register_speculative_detector,
)
from app.domains.ai_engine.common import (
    build_detect_history_block,
    trace_line,
)
from app.domains.ai_engine.turn_context import (
    AgentRunRecord,
    TurnContext,
)
from app.domains.asset_allocation.services.aa_engine.overrides import (
    _ALLOWED_OVERRIDE_KEYS,
    with_chat_overrides,
)

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
goal-based asset allocation. Pick exactly one of six modes.

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
  counterfactual_explore here. Must specify `overrides`.
  Multiple keys allowed in one action ("what if risk were 7 AND corpus
  were ₹1 crore" → both keys). Does NOT persist on this turn.
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
  NEVER redirect risk or allocation tuning: "more risk", "less conservative", "higher risk allocation", "do allocation with more risk", "change my risk" → `clarify` (no number) or `counterfactual_explore` (number given). The word "which" in "allocation which more risk" does NOT mean fund-picking.

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
- "save this with ₹1 crore corpus"     → counterfactual_explore,
                                         overrides={total_corpus: 10000000}
- "do asset allocation which more risk" → clarify (current risk from snapshot)
- "allocation with more risk"          → clarify or counterfactual_explore if
                                         a score is given — NOT redirect
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

CUSTOMER_RECORD shape (treat fields not present as unknown):

  risk_score: number — customer's effective risk score (1-10)
  risk_profile_category: string — the named band that maps the score, one of
                       Conservative / Moderately Conservative / Moderate /
                       Moderately Aggressive / Aggressive. Use this as the
                       primary way to describe the customer's investing style
                       (see the shared house-style rule above).
  age: int
  total_corpus_inr: number — total invested corpus, market value in ₹
  total_corpus_indian: string — same value pre-formatted in Indian notation
  emergency_fund_months: int — how many months of the customer's
                       household expenses the emergency reserve covers.
                       3 is the default; 6 applies when the customer's
                       primary income comes from their portfolio
                       (typically retirees or full-time investors).
  monthly_household_expense_inr: number — the customer's stated monthly
                       household spend (₹).
  monthly_household_expense_indian: string — same value, pre-formatted
                       in Indian notation (copy verbatim per the
                       money-formatting rule).
  plan_target_pct: {equity, debt, others} — the AA engine's RECOMMENDED
                       deployment mix as percentages. This is what the engine
                       suggests the customer should hold, NOT what they
                       currently hold. Read the field name literally: it is
                       the plan's target, not the customer's position.
  plan_target_inr: {equity, debt, others} — recommended deployment in ₹.
  plan_target_indian: {equity, debt, others} — pre-formatted strings of
                          the recommended deployment.
  your_actual_holdings_today_pct: {equity, debt, cash, others} — the
                   customer's TRUE CURRENT holdings (% of portfolio),
                   summed from their actual portfolio allocation rows.
                   Note the four buckets: cash is preserved separately
                   here even though the engine only models three. May be
                   ABSENT when the customer has no portfolio data yet —
                   in that case do not claim a current mix; only describe
                   the plan target.
  your_actual_holdings_today_inr / your_actual_holdings_today_indian:
                   same as above, in ₹ / pre-formatted.

When the customer asks "is my allocation right?", "is my portfolio aligned
with my goals?", or "what is my mix?", compare
your_actual_holdings_today vs plan_target and describe the gap.
NEVER quote plan_target numbers in a sentence that says "you're holding",
"you have", or "your current" — those numbers are the engine's plan, not
their holdings. Use your_actual_holdings_today_* for any "you're holding"
phrasing.
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
  rounding (e.g., plan_target_inr may not sum exactly to
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
                             CUSTOMER_RECORD directly tied to the question. Do
                             NOT list every bucket or restate the full
                             plan. Length: 4-7 sentences.
  educate                  — they're asking what something means. Lead with
                             a one-line plain-English definition, then
                             anchor it in at least one number from
                             CUSTOMER_RECORD that's specific to this customer.
                             Length: 4-7 sentences.
  recompute_full           — re-ran with current saved inputs. Acknowledge
                             the re-run briefly and highlight what's
                             noteworthy. Length: 6-10 sentences.
  counterfactual_explore   — hypothetical-only result. Make clear this is
                             a hypothetical for comparison, not the saved
                             plan; reference the saved plan as the
                             baseline but don't reprint it in full.
                             Length: 6-10 sentences.
"""


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


@register_speculative_detector("asset_allocation")
async def _speculative_detect(ctx: TurnContext) -> ChatAction | None:
    """Follow-up action detect, started by the brain concurrently with the
    intent classifier (audit F4). Pure read — same call `handle` would make."""
    last_alloc = ctx.last_agent_runs.get("asset_allocation")
    if last_alloc is None:
        return None
    action = await _detect_action(last_alloc, ctx)
    return _coerce_misclassified_redirect_action(ctx.user_question, action, last_alloc)


@register("asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Sole entry point for chat turns in this intent family."""
    last_alloc = ctx.last_agent_runs.get("asset_allocation")

    if last_alloc is None:
        # First turn (or no persisted snapshot in this session) → run engine.
        return await _first_turn_run_engine(ctx)

    # Follow-up turn → decide what to do. Prefer the brain's speculative
    # detect result (already includes the coerce step); serial detect is the
    # fallback when speculation didn't run or failed.
    try:
        action = await consume_speculative_detect(ctx)
        if action is None:
            action = await _detect_action(last_alloc, ctx)
            action = _coerce_misclassified_redirect_action(
                ctx.user_question, action, last_alloc
            )
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

    logger.info(
        "asset_allocation_chat mode=%s overrides=%s", action.mode, action.overrides
    )
    trace_line(f"asset_allocation_chat mode={action.mode}")

    return await _dispatch_action(action, last_alloc, ctx)


# ---------------------------------------------------------------------------
# Mode dispatcher
# ---------------------------------------------------------------------------


async def _dispatch_action(
    action: ChatAction,
    last_alloc: AgentRunRecord,
    ctx: TurnContext,
) -> ChatHandlerResult:
    if action.mode in ("narrate", "educate"):
        try:
            output = _rehydrate_last_alloc_output(last_alloc)
        except Exception as exc:
            logger.error(
                "rehydrate_last_alloc_output_failed mode=%s error_class=%s",
                action.mode,
                type(exc).__name__,
            )
            return ChatHandlerResult(
                text=(
                    "I couldn't load your last plan to answer that. "
                    "Try asking me to redo the plan and we'll work from there."
                )
            )
        text = await _format_or_fallback(
            ctx=ctx,
            output=output,
            action_mode=action.mode,
            spine_mode="full",
        )
        return ChatHandlerResult(text=text)

    if action.mode == "counterfactual_explore":
        return await _counterfactual_explore(last_alloc, ctx, action.overrides or {})

    if action.mode == "clarify":
        text = action.clarification_question or _DEFAULT_CLARIFY_FALLBACK
        return ChatHandlerResult(text=text)

    if action.mode == "recompute_full":
        return await _recompute_full(ctx)

    # redirect (default)
    reason = action.redirect_reason or "change your plan"
    text = await format_relay_or_canned(
        ctx=ctx,
        module_name="asset_allocation",
        message=_REDIRECT_TEMPLATE.format(reason=reason),
    )
    return ChatHandlerResult(text=text)


async def _reply_with_allocation_tables(
    *,
    ctx: TurnContext,
    output: Any,
    action_mode: ActionMode,
    spine_mode: str,
) -> str:
    """Return a natural-language allocation reply tailored to the customer's question.

    Routes through the shared answer_formatter (PI voice, Indian money notation,
    named risk band, reasoning kept out of the reply) — exactly like the
    narrate/educate follow-ups — with the deterministic ``build_fallback_brief``
    as the last-resort fallback when the formatter LLM is unavailable. This keeps
    every allocation reply (first turn, recompute, counterfactual) free of raw
    rupee figures and raw risk scores.

    The previous behaviour of returning the full DB-parity tables markdown
    moved to the per-row DB writes — chat surfaces a customer-facing answer only.
    """
    try:
        text = await _format_or_fallback(
            ctx=ctx,
            output=output,
            action_mode=action_mode,
            spine_mode=spine_mode,
        )
    except Exception:
        # Partial engine output (e.g. missing asset_class_breakdown on a stub run)
        # can break facts-pack/brief construction — surface a minimal
        # acknowledgement rather than crash the turn.
        logger.exception("allocation reply formatting failed; emitting minimal reply")
        text = (
            "I worked out an allocation for you. Open the allocation tab to "
            "see the per-bucket details."
        )
    return text


def _risk_score_from_snapshot(last_alloc: AgentRunRecord) -> float:
    payload = (last_alloc.output_payload or {}).get("allocation_result") or {}
    cs = payload.get("client_summary") or {}
    return _float(cs.get("effective_risk_score"), 7.0)


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_misclassified_redirect_action(
    question: str,
    action: ChatAction,
    last_alloc: AgentRunRecord,
) -> ChatAction:
    """Recover when Haiku wrongly redirects risk/allocation tuning to Profile."""
    if action.mode != "redirect":
        return action
    reason = (action.redirect_reason or "").lower()
    if "fund-level" not in reason and "fund level" not in reason:
        return action

    q = question.lower()
    fund_phrases = (
        "switch from",
        "switch to",
        "which fund",
        "which scheme",
        "large-cap fund",
        "large cap fund",
        "mid-cap fund",
        " folio ",
    )
    if any(p in q for p in fund_phrases):
        return action

    tuning_markers = (
        "risk",
        "allocat",
        "conservative",
        "aggressive",
        "equity",
        "debt",
        "corpus",
        "income",
        "expense",
        "emergency",
    )
    if not any(m in q for m in tuning_markers):
        return action

    risk = _risk_score_from_snapshot(last_alloc)
    num = re.search(r"\b(\d+(?:\.\d+)?)\b", question)
    if num:
        return ChatAction(
            mode="counterfactual_explore",
            overrides={"effective_risk_score": float(num.group(1))},
        )
    if re.search(r"more\s+risk|higher\s+risk|more\s+aggressive|take\s+more\s+risk", q):
        target = min(10.0, round(risk + 1.5, 1))
        return ChatAction(
            mode="counterfactual_explore",
            overrides={"effective_risk_score": target},
        )
    if re.search(r"less\s+risk|lower\s+risk|more\s+conservative|less\s+aggressive", q):
        target = max(1.0, round(risk - 1.5, 1))
        return ChatAction(
            mode="counterfactual_explore",
            overrides={"effective_risk_score": target},
        )
    direction = "higher" if re.search(r"more|higher|aggressive", q) else "lower"
    suggested = (
        min(10.0, round(risk + 1.5, 1))
        if direction == "higher"
        else max(1.0, round(risk - 1.5, 1))
    )
    return ChatAction(
        mode="clarify",
        clarification_question=(
            f"Your current risk score is {risk:.1f}. "
            f"Would {suggested:.1f} work for a {direction}-risk allocation?"
        ),
    )


# ---------------------------------------------------------------------------
# Per-mode handlers
# ---------------------------------------------------------------------------


async def _first_turn_run_engine(ctx: TurnContext) -> ChatHandlerResult:
    """Run the engine on a fresh session (or session with no allocation yet)."""
    outcome = await compute_allocation_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        persist_recommendation=ctx.db is not None,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="full",
        gate_on_zero_corpus=True,
    )
    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(
            text="I couldn't produce an allocation right now. Please try again."
        )
    text = await _reply_with_allocation_tables(
        ctx=ctx,
        output=outcome.result,
        action_mode="compute",
        spine_mode="full",
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=outcome.allocation_snapshot_id,
        asset_allocation_run_id=outcome.asset_allocation_run_id,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
    )


async def _counterfactual_explore(
    last_alloc: AgentRunRecord,
    ctx: TurnContext,
    overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical."""
    if not overrides or not _validate_overrides(overrides):
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="asset_allocation",
            message=_INVALID_OVERRIDE_TEMPLATE,
        )
        return ChatHandlerResult(text=text)

    chat_ctx = with_chat_overrides(ctx, overrides)
    outcome = await compute_allocation_result(
        ctx.user_ctx,
        ctx.user_question,
        db=None,  # NO writes
        persist_recommendation=False,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="counterfactual",
        chat_ctx=chat_ctx,
        gate_on_zero_corpus=True,
    )

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)
    if outcome.result is None:
        return ChatHandlerResult(text="I couldn't compute that hypothetical right now.")

    text = await _reply_with_allocation_tables(
        ctx=ctx,
        output=outcome.result,
        action_mode="counterfactual_explore",
        spine_mode="counterfactual",
    )
    return ChatHandlerResult(text=text)


async def _recompute_full(ctx: TurnContext) -> ChatHandlerResult:
    """Same as first-turn but explicitly user-requested re-run."""
    return await _first_turn_run_engine(ctx)


# ---------------------------------------------------------------------------
# Override helpers
# ---------------------------------------------------------------------------


def _validate_overrides(overrides: dict[str, Any]) -> bool:
    """All override keys must be in the allow-list."""
    return all(k in _ALLOWED_OVERRIDE_KEYS for k in overrides.keys())


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
    alloc = (
        (output_payload.get("allocation_result") or {})
        if isinstance(output_payload, dict)
        else {}
    )
    if not alloc:
        return {}

    # Bucket allocations: keep only what classification needs.
    slim_buckets = []
    for b in alloc.get("bucket_allocations", []) or []:
        slim_buckets.append(
            {
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
            }
        )

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
    last_alloc: AgentRunRecord,
    ctx: TurnContext,
) -> ChatAction:
    """One Haiku call returning a ChatAction. Uses the shared classify_action."""
    slim = _slim_snapshot(last_alloc.output_payload)
    snapshot_json = json.dumps(slim, default=str)
    if len(snapshot_json) > _DETECT_SNAPSHOT_BUDGET:
        # Trim structurally first (drop per-goal detail — the mode classifier
        # doesn't need it) so the classifier gets valid JSON; hard-slice only
        # as a last resort (a mid-string cut sends malformed JSON).
        slim["buckets"] = [
            {k: v for k, v in b.items() if k != "goals"}
            for b in slim.get("buckets") or []
        ]
        trimmed_json = json.dumps(slim, default=str)
        logger.info(
            "detect_action_snapshot_trimmed original_len=%d trimmed_len=%d budget=%d",
            len(snapshot_json),
            len(trimmed_json),
            _DETECT_SNAPSHOT_BUDGET,
        )
        snapshot_json = trimmed_json[:_DETECT_SNAPSHOT_BUDGET]

    history_block = build_detect_history_block(ctx.conversation_history)
    history_section = (
        f"\n\nRecent conversation (oldest → newest):\n{history_block}"
        if history_block
        else ""
    )
    user_block = (
        f"Customer's question: {ctx.user_question}\n\n"
        f"Saved plan snapshot (slim):\n{snapshot_json}"
        f"{history_section}"
    )
    return await classify_action(
        action_model=ChatAction,
        system_prompt=_DETECT_SYSTEM,
        user_block=user_block,
        api_key=get_settings().get_anthropic_asset_allocation_key(),
    )


# ---------------------------------------------------------------------------
# Formatter helpers
# ---------------------------------------------------------------------------


def _profile_dict(ctx: TurnContext) -> dict[str, Any]:
    """Pull the customer's profile fields the formatter cares about."""
    user = ctx.user_ctx
    return {
        "age": getattr(user, "age", None)
        or _years_since(getattr(user, "date_of_birth", None)),
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
    action_mode: ActionMode,
    spine_mode: str,
) -> str:
    """Run the formatter; fall back to the templated brief on failure."""
    current_mix = compute_current_asset_class_mix(ctx.user_ctx)
    # Income feeds the allocation engine but isn't in its client_summary, so
    # pass it into the facts pack directly — same source input_builder reads.
    annual_income = pf.annual_income_pfp(
        getattr(ctx.user_ctx, "personal_finance_profile", None)
    )
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_aa_facts_pack(
            output, current_mix=current_mix, annual_income=annual_income
        ),
        body_prompt=_AA_FORMATTER_BODY,
        module_name="asset_allocation",
        action_mode=action_mode,
        profile=_profile_dict(ctx),
        build_fallback=lambda: build_fallback_brief(output, spine_mode),
    )
