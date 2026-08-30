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
from app.domains.ai_engine.common import build_detect_history_block, ensure_ai_agents_path
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
from app.domains.rebalancing.services.saved_plan_service import ORIGIN_CANDIDATE
from app.domains.rebalancing.services.rebal_engine.formatter import (
    build_fallback_rebal_brief,
)
from app.domains.rebalancing.services.rebal_engine.overrides import (
    _REBAL_ALLOWED_OVERRIDE_KEYS,
    with_chat_overrides,
)

from app.core.observability import capture_preference_unserved
from app.domains.mutual_funds.services.fund_ranking_lookup import (
    ranking_by_isin,
    resolve_ranked_fund,
)
from app.domains.mutual_funds.services.investment_preferences import (
    normalize_tilt,
)

ensure_ai_agents_path()
from house_view import load_house_view  # noqa: E402  (bare import via ensure_ai_agents_path)

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
            "carryforward_lt_loss_inr, additional_cash_inr, asset_class_tilt, "
            "pure_equity_only. Never emit asset_class_tilt or pure_equity_only "
            "yourself — set the tilt_* / scope_only_asset_classes fields "
            "instead; the app builds them."
        ),
    )
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)
    scope_only_asset_classes: Optional[list[Literal["equity", "debt", "others"]]] = Field(
        default=None,
        description=(
            "For counterfactual_explore. 'Only equity funds' → ['equity']; "
            "'no gold' → ['equity', 'debt']. Asset-class level only — fund "
            "categories like 'large cap' belong in allowed_categories."
        ),
    )
    tilt_asset_class: Optional[Literal["equity", "debt", "others"]] = Field(
        default=None,
        description=(
            "For counterfactual_explore. The asset class whose exposure the "
            "customer wants changed ('increase my equity exposure' → 'equity')."
        ),
    )
    tilt_delta_pp: Optional[float] = Field(
        default=None,
        description=(
            "Relative change in percentage points, ONLY when the customer "
            "states one ('by 10 percent' → 10, 'reduce by 5' → -5). NEVER "
            "invent a number — leave unset when none was said."
        ),
    )
    tilt_target_pct: Optional[float] = Field(
        default=None,
        description=(
            "Absolute target percent, ONLY when the customer states one "
            "('take equity to 70%' → 70). NEVER invent a number."
        ),
    )
    excluded_categories: Optional[list[str]] = Field(
        default=None,
        description=(
            "For consolidate. Categories to EXCLUDE from new buys — the "
            "customer's words verbatim ('no ELSS', 'nothing with a lock-in' → "
            "['elss'])."
        ),
    )
    category_weights: Optional[dict[str, float]] = Field(
        default=None,
        description=(
            "For consolidate. 'More mid cap' asks: {customer's category words: "
            "requested percent of buys 0-100}. A stated percent → that number; "
            "NO stated percent → 0 (sentinel: the app applies its documented "
            "default step and discloses it)."
        ),
    )
    named_fund: Optional[str] = Field(
        default=None,
        description=(
            "A specific scheme the customer names ('use Parag Parikh Flexi "
            "Cap', 'why not Quant Small Cap?') — the fund words verbatim."
        ),
    )
    named_fund_intent: Optional[Literal["include", "why_not"]] = Field(
        default=None,
        description=(
            "With named_fund: 'use/include/switch to X' → include; "
            "'why not X / why didn't you pick X' → why_not. Both ride on "
            "mode narrate."
        ),
    )
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
    "cash to deploy, or your equity/debt/gold exposure). Other changes — like "
    "deferring the rebalance — aren't supported yet. If you'd like a 'what if' "
    "on the supported inputs, just say so."
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
    asset_class_tilt:          number map — INTERNAL, never emit it yourself; for exposure asks fill the tilt_* / scope_only_asset_classes FIELDS below and the app builds this key
    pure_equity_only:          true — INTERNAL, never emit yourself; the app sets it for an "only/all/100% equity" ask
  Multiple keys are allowed in one action ("what if my tax rate were 20%
  AND I had ₹50K in carry-forward losses?"). Does NOT persist on this turn.
  EXPOSURE ASKS are ALWAYS counterfactual_explore — any request to change how
  much equity/debt/gold they hold ("increase my equity", "take equity to 70%",
  "only/all equity", "make it 100% equity", "add some gold"), even mid-
  conversation and even alongside risk-score talk. NEVER instead clarify about
  their risk score, redirect to Profile, or call it a goal/profile conflict —
  the tilt covers it. Fill tilt_asset_class (equity / debt / others — gold and
  "safer" map here too): "make it safer / more conservative / less risky /
  reduce risk" → tilt_asset_class="debt"; "make it aggressive / riskier / more
  growth" → tilt_asset_class="equity"; "add gold / more gold" →
  tilt_asset_class="others". Set tilt_delta_pp for a stated relative number OR
  tilt_target_pct for a stated absolute one ("100%" → 100) — never invent one,
  leave unset if none given (the app applies a documented step). "Only/all
  <asset class>" → scope_only_asset_classes. A "which funds to switch?" tacked
  on is still the tilt (the app returns the trades). A follow-up that ADJUSTS a
  prior hypothetical is STILL an exposure tilt — "add gold to that" →
  tilt_asset_class="others"; "now make it safer" → tilt_asset_class="debt"; "a
  bit more equity than that" → tilt_asset_class="equity". Recompute it; NEVER
  reply that you "can't adjust the plan on the fly" or "can only show what's
  already computed" — you CAN, via the tilt.
- "compute" — they explicitly ask to re-run with current portfolio state
  ("rebalance again", "redo this with my latest holdings"). No overrides.
- "clarify" — they want us to DO something to the plan but have not given the
  value we need to do it. Compose a concise question in `clarification_question`.
  This mode is ONLY for a missing input to an action. It is NOT for questions
  about the plan or its numbers: "why is there a discrepancy?", "that doesn't
  match what my plan shows", "no, I meant the target", "I'm trying to understand
  this" are ALL narrate — the snapshot has the numbers, so answer from it.
  NEVER ask the customer to read their own screen back to us (which row, what
  label, which heading) — if their figure disagrees with ours, narrate ours and
  explain the difference. NEVER re-ask something the recent conversation shows
  we already asked; if they answered, use it, and if they didn't, answer anyway
  with what we have.
- "consolidate" — they want FEWER new-buy funds, or the new money restricted
  to / weighted toward / kept out of specific fund categories. This reshapes
  only the BUY side of the plan (sells and tax are untouched). Optional fields:
    category_weights: dict — "more mid cap", "at least 30% in small cap" →
      {customer's words: percent 0-100}. A stated percent → that number; NO
      stated percent → 0 (sentinel — the app applies its documented default
      step and discloses it). Category words verbatim, never internal keys.
    excluded_categories: list[str] — "no ELSS", "nothing with a lock-in",
      "skip sectoral funds" → the words verbatim (["elss"], ["sectoral"]).
  CONTRADICTION: if the same turn excludes a category AND asks for more of it
  (or scopes to an asset class that excludes a requested category — "only debt
  funds but more mid cap"), emit clarify instead, naming the conflict in
  clarification_question.
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
  supported yet). Also concepts we don't rank funds in at all (ESG,
  international/overseas themes). Set `redirect_reason` to a short description.
  (An equity/debt/gold exposure change is NEVER redirect — see EXPOSURE ASKS.)

NAMED FUNDS: when the customer names a specific scheme, set named_fund (their
words verbatim) + named_fund_intent — "use/switch to X" → include, "why not X /
why didn't you pick X" → why_not — and emit mode narrate for BOTH intents (the
app answers from the ranking data; inclusion gets an honest "coming later").

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

counterfactual_explore (a value to test, or an exposure change — all
counterfactual_explore; commit-shaped "save with…" still counts):
- "what if my tax rate were 20%?"           → overrides={effective_tax_rate: 20}
- "what if I had ₹2L more to deploy?"       → overrides={additional_cash_inr: 200000}
- "tax 20% AND ₹50K short-term losses"      → overrides={effective_tax_rate: 20,
                                              carryforward_st_loss_inr: 50000}
- "increase my equity by 10 percent"        → tilt_asset_class="equity", tilt_delta_pp=10
- "take my equity exposure to 70%"          → tilt_asset_class="equity", tilt_target_pct=70
- "increase my equity exposure"             → tilt_asset_class="equity" (no number set)
- "only equity funds" / "all equity, no
  debt" / "make it 100% equity"             → scope_only_asset_classes=["equity"]
- "keep 100% equity, tell me which funds
  to switch — I accept the risk"            → scope_only_asset_classes=["equity"]
                                              (NOT consolidate; do NOT clarify risk score)
- "just make it safer"                      → tilt_asset_class="debt" (no number)
- "add a little gold as well"               → tilt_asset_class="others" (no number)

consolidate (all mode consolidate — fewer buys, or buys restricted/reweighted/
excluded by category):
- "reduce my trades, too many"              → both fields null (handler asks once)
- "consolidate into 5 funds"                → target_fund_count=5
- (we just asked how many) "5"              → target_fund_count=5 (history-fill, don't re-ask)
- "only invest in largecap and midcap"      → allowed_categories=["large cap","mid cap"]
- "I want more mid cap" / "more mid cap
  than large cap"                           → category_weights={"mid cap": 0}
                                              (no percent → sentinel 0; "30% small cap" → {..:30})
- "nothing with a lock-in"                  → excluded_categories=["elss"]
- "no sectoral funds"                       → excluded_categories=["sectoral"]
- "only equity, more mid cap, max 4 funds"  → category_weights={"mid cap": 0},
                                              target_fund_count=4 (category+count win here)

named funds (mode narrate for both intents):
- "use Parag Parikh Flexi Cap instead"      → narrate, named_fund="Parag
                                              Parikh Flexi Cap",
                                              named_fund_intent="include"
- "why didn't you pick Quant Small Cap?"    → narrate, named_fund="Quant
                                              Small Cap",
                                              named_fund_intent="why_not"

compute:
- "rebalance my portfolio"                  → compute
- "redo with my latest holdings"            → compute

redirect (out of scope, or override outside the allow-list):
- "what if I delayed by 3 months?"          → redirect, "delay rebalance by N months"
- "don't sell my HDFC Top 100"              → redirect, "lock specific holdings"
- "only ESG funds please"                   → redirect, "we don't rank ESG funds"

clarify (an action we can take, missing only its value — or a contradiction):
- "I want to reduce tax"                    → clarify, "Your effective tax rate
                                              is X% — would 20% feel right?"
- "only debt funds but add more mid cap"    → clarify, "Mid cap is equity, so
                                              debt-only would exclude it —
                                              which matters more?"

NOT clarify — these are questions about the plan, so narrate:
- "why is there a discrepancy?"             → narrate
- "my plan shows 83% equity, not 95%"       → narrate
- "no no, not today's picture — the target" → narrate
- "I'm trying to understand this"           → narrate
- "how many trades is that now?"            → narrate (answer the count from the
                                              plan; do NOT re-run consolidate)
"""

_REBAL_FORMATTER_BODY = """You are answering a customer's question about a
mutual-fund rebalancing recommendation. The shared house-style rules above apply.

The CUSTOMER_RECORD has this shape (treat fields not present as unknown):

  total_portfolio_inr / total_portfolio_indian — total invested corpus across all holdings
  buys_total_inr / buys_total_indian — sum of recommended buy amounts
  sells_total_inr / sells_total_indian — sum of recommended sell amounts
  tax_impact_inr / tax_impact_indian — estimated tax payable on the sells
  tax_treatment — how that tax bill splits by holding period:
      ltcg_realised_inr / _indian         — long-term gains realised (lower LTCG rate)
      stcg_realised_inr / _indian         — short-term gains realised
      stcg_offset_by_losses_inr / _indian — STCG cancelled out by short-term losses
    Use this for any "is this / make this tax-efficient" question. A low or zero
    stcg_realised is the proof the plan is ALREADY tax-optimised: it sells
    long-term units first and leaves short-term units untouched (short-term is
    sold only on a forced exit). State that with the figures. Do NOT invent a
    different reason (e.g. "we picked funds with lower embedded gains" or
    "shorter manager tenures") — ground the "why" in LOGIC_REFERENCE when present.
  trade_count: int — number of distinct buy/sell trades in the recommendation

  current_asset_class_mix_pct / _inr / _indian — {equity, debt, others}: what the
    customer holds TODAY, before any of these trades.
  target_asset_class_mix_pct / _inr / _indian — {equity, debt, others}: what they
    will hold AFTER this plan's trades execute. This is the plan's target mix and
    it is the SAME number the Invest page shows on its Current-vs-Target bars.

  ideal_asset_class_mix_pct — {equity, debt, others}: the split their goals and
    risk profile alone call for, ignoring what they currently hold. Optional.

  These three are different questions and must never be swapped:
    "what do I hold now?"              → current_*
    "what's the target / what is the
     plan moving me toward?"           → target_*
    "what SHOULD my mix be?"           → ideal_*

  The ideal and the target legitimately differ: the ideal is the destination on
  paper, the target is what THIS plan can reach given what they already hold and
  what it is willing to trade (chiefly, it avoids short-term capital-gains tax by
  selling only long-held units, and keeps holdings still worth owning). WHENEVER
  the target sits well away from the ideal on equity (more than ~5 points),
  PROACTIVELY bridge the two in ONE sentence — quote both and frame the target as
  a STEP TOWARD the ideal, not a rival number, naming the reason it stops short
  from the tax figures / ``warnings`` (e.g. "your long-term ideal is ~40% equity;
  from today's 95% this plan moves you to 74% — a big step, held back from a full
  move mainly to avoid short-term-gains tax"). The allocation view may have just
  shown the customer the ideal, so a bare target reads as a contradiction. Ground
  the "why partway" in the tax figures / ``warnings`` — do NOT invent lock-ins,
  untradeable holdings or staged journeys that nothing in CUSTOMER_RECORD supports.

  When the customer says a number from their plan disagrees with yours, they are
  almost certainly reading their own screen correctly. Quote target_* and
  reconcile against it. If a block you need is absent, say you don't have that
  figure — never substitute one of the other two.

  NEVER state an asset-class mix or percentage that is not present verbatim in
  CUSTOMER_RECORD. Do not average two mixes, do not interpolate a "middle
  ground", and never invent a compromise split (e.g. "we could trim to
  65/28/7") — if the exact figure is not in the facts, do not give one. Bucket-
  level splits (goal_buckets.planned_split_pct) are PER-BUCKET, never the whole
  portfolio — never present a bucket's equity % as the overall mix.

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

  fund_house_view: optional — present only on judgement-style rebalancing turns.
    Prozpr's OWN current market stance (our view on large/mid/small-cap equities,
    debt, gold). Use it to FRAME why the plan's direction makes sense ("we're
    cautious on small caps, so the plan trims them"). It is our voice — never name
    or attribute a view to any fund house — and it NEVER overrides the computed
    numbers; the trades stand on their own. Ignore it for purely factual questions.

  fund_actions: per-fund actions (top 30 by exposure; more_holdings_count carries
    any overflow for "and N smaller holdings"). Each: fund_name (customer-facing
    scheme name, cite verbatim), sub_category, asset_subgroup (do NOT surface),
    and current/buy/sell/planned_final (_inr + pre-formatted _indian; planned_final
    = current + buy − sell).
    On any turn that PRESENTS A PLAN (compute, counterfactual_explore, consolidate)
    always include a short fund-level trade list — the largest ~3-5 buys and ~3-5
    sells by fund_name + _indian amount — so the customer sees concrete funds, not
    only categories. For a "what will I hold after?" view, list planned_final > 0,
    biggest first. For narrate/educate, fund detail only when the question is
    fund-specific.

  constraint_impact: optional — on a consolidate OR equity-tilt/scope turn. Fields:
      recommended_mix_pct / requested_mix_pct: {equity, debt, others} — the
        recommended plan vs the plan reshaped to the customer's request. On a
        tilt/scope turn these two are the ONLY asset-class figures you may cite
        for the contrast — verbatim, never a third number. requested_mix_pct is
        where that plan LANDS (may fall short of a round 100% — give the real
        figure, don't round to what they asked).
      tilt_note: directive string — when present, FOLLOW IT EXACTLY.
      buy_changes_vs_recommended: [{fund, recommended_indian, requested_indian,
        change_indian}] — on a tilt turn, the per-fund buy DIFFERENCE from the
        recommended plan (biggest first). Show change_indian ("+₹2.5 lakh into
        X") rather than the absolute requested buys.
      target_mix_pct: the ideal target mix. unconstrained_mix_pct /
        constrained_mix_pct: plan mix before vs after the constraint.
        largest_deviations [[label, delta_pct],...]: biggest moves vs target (may
        be ~0 for an intra-equity ask). buy_mix_by_category {unconstrained,
        constrained}: new-buy split by category — use it when asset-class deltas
        are flat. risk_profile: label (may be null).
      defaulted_fund_count: int — present ONLY when WE picked the count. Own it:
        "you didn't say a number, so I spread it across 5 funds — say the word for 3."

  goal_buckets: optional list (present when goals drove the rebalance). Per bucket:
      horizon_label (use verbatim, e.g. "Long-term (> 5 yrs)"); goals [{name,
      horizon_months, amount_needed_indian, priority}] — priority "non_negotiable"/
      "negotiable" → say "must-meet"/"flexible"; total_goal_amount_indian /
      allocated_amount_indian; planned_split_pct {equity,debt,others} the engine
      targeted for THIS bucket (why each bucket's mix is what it is).
    When it clarifies the answer, tie trades to the bucket/goal ("trimming equity
    in your short-term bucket — the house goal is ~18 months away"). Don't
    enumerate every bucket; only the one(s) the question touches. Absent → answer
    from the trade/asset-class facts.

ACTION_MODE tells you the situation. Per-mode behavior:

  compute    — a rebalancing recommendation we just produced; introduce it shaped by
               the customer's question. Cover: the headline (trade_count, total
               trade volume from buys_total_indian / sells_total_indian, and
               tax_impact_indian if non-zero), the 1-2 biggest moves at
               sub_category level, the resulting target_asset_class_mix_indian
               (state it as where the plan lands them, and contrast with
               current_asset_class_mix_indian when the shift is the point), a
               short fund-level trade list (the largest few buys and sells by
               fund_name + amount, from fund_actions), and
               any warning that meaningfully shapes the picture. Lead with the
               headline unless the customer's question is specifically about
               tax or a specific fund — then lead with that. If trade_count is
               0, skip the trade details — lead with the alignment fact (e.g.,
               "your portfolio is already aligned with your target mix") and
               briefly mention current_asset_class_mix_indian. Length: 8-12
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
  counterfactual_explore — a hypothetical plan the customer ASKED FOR (e.g. a
               higher-equity tilt, "only equity", "make it 100% equity").
               COMPLY FIRST: lead with the plan they requested — the biggest
               buys/sells it makes and where its asset-class mix LANDS — framed
               as a hypothetical for comparison, not the saved plan. When
               constraint_impact carries recommended_mix_pct / requested_mix_pct,
               state the contrast using ONLY those two figures ("your
               recommended plan is X% equity; the version you asked for lands at
               Y%") and follow tilt_note. Then add ONE grounded caution about
               the deviation. Do NOT lecture, do NOT refuse, do NOT push back
               with clarifying questions, and NEVER offer an intermediate
               "compromise" mix (e.g. "we could trim to 65/28/7") — that number
               is not in the facts and must never appear. For the funds, when
               constraint_impact carries buy_changes_vs_recommended, show the
               DIFFERENCE your tilt makes vs the recommended plan — e.g. "vs our
               recommendation it puts +₹2.5 lakh into ICICI Large Cap and −₹3
               lakh into the arbitrage fund" — NOT the requested plan's absolute
               buys; the change is what the customer wants to see. If a plain
               tax/cash counterfactual (no tilt, no buy_changes), fall back to a
               short absolute buy/sell list from fund_actions and reference the
               saved recommendation as baseline. Length: 6-10 sentences.
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
               unchanged. If constraint_impact carries defaulted_fund_count,
               LEAD by owning that we picked the number and how to change it.
               Length: 6-10 sentences.
"""

_REDIRECT_TEMPLATE = (
    "To {reason}, head to your **Profile** or **Holdings** page and update "
    "the relevant inputs — I'll regenerate the rebalancing plan automatically."
)

# Honest decline for "don't sell / lock this fund" — Profile can't pin a holding
# out of the rebalance, so pointing there would mislead. Sell-side locks are a
# planned feature, not yet available.
_LOCK_NOT_SUPPORTED = (
    "I can't hold a specific fund out of the rebalance from chat yet — the plan "
    "trims and adds across your whole portfolio as one set of trades, and pinning "
    "one fund to keep isn't something I can do here right now. It's on our list "
    "to support. What I can do is walk you through *why* a fund is being sold, if "
    "that would help you decide."
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

# Used when the customer asked to consolidate, we asked how many funds, and their
# reply still carried no number ("as few as possible", "you decide"). Doing the
# work with a stated default beats asking the same question twice.
_DEFAULT_CONSOLIDATE_FUND_COUNT = 5

# "More mid cap" with no stated percent -> raise that category to this share of
# the total buy budget (spec 2026-08-24 defaults table; always disclosed).
_DEFAULT_WEIGHT_STEP = 0.10


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
    # Prozpr-only house view, gated on the classifier's tools_needed. A rebalance is
    # advice, so when the view is called it frames the trades; the flow sets scope.
    want_view = "fund_house_view" in (getattr(ctx, "tools_needed", ()) or ())
    fund_house_view = load_house_view(prozpr_only=True) if want_view else None
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_rebal_facts_pack(
            response,
            goal_buckets=goal_buckets,
            constraint_impact=constraint_impact,
            is_rerun=is_rerun,
            fund_house_view=fund_house_view,
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


async def _last_action_mode(ctx: TurnContext) -> Optional[str]:
    """The most recent persisted ``action_mode`` for rebalancing in this session.

    Used to stop clarify from repeating. Deliberately NOT read off
    ``ctx.last_agent_runs`` — that loader keeps only rows carrying an
    output_payload (the engine runs), so formatter-only turns like a clarify are
    invisible there.

    Runs in a savepoint: a failure here must never poison the outer session, and
    degrades to None (ask once more) rather than breaking the turn.
    """
    if ctx.db is None or ctx.session_id is None:
        return None
    from sqlalchemy import select

    from app.domains.chat.models.chat_ai_module_run import ChatAiModuleRun

    try:
        async with ctx.db.begin_nested():
            stmt = (
                select(ChatAiModuleRun.action_mode)
                .where(ChatAiModuleRun.session_id == ctx.session_id)
                .where(ChatAiModuleRun.module == "rebalancing")
                .where(ChatAiModuleRun.action_mode.isnot(None))
                .order_by(ChatAiModuleRun.created_at.desc())
                .limit(1)
            )
            return (await ctx.db.execute(stmt)).scalar_one_or_none()
    except Exception:
        logger.warning("last_action_mode lookup failed", exc_info=True)
        return None


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

    return await _handle_action(ctx, action, last_run)


async def _handle_action(
    ctx: TurnContext,
    action: RebalanceAction,
    last_run: AgentRunRecord,
) -> ChatHandlerResult:
    """Dispatch one detected follow-up action (mode ladder extracted from
    ``handle``; preference routing added by the 2026-08-24 spec)."""
    if action.named_fund:
        return await _handle_named_fund(ctx, action)

    # Honor an extracted equity-exposure tilt/scope REGARDLESS of the mode label
    # the detector attached. Conversational "100% equity" asks sometimes get
    # labelled consolidate / clarify / redirect (and scope_only was then silently
    # dropped); the extracted fields are authoritative — always comply-and-caution.
    if action.tilt_asset_class or action.scope_only_asset_classes:
        return await _handle_preference_counterfactual(ctx, action)

    if action.mode == "clarify":
        # Ask at most ONCE in a row. A customer disputing a number ("that's not
        # what my plan says") reads as "a direction without a value" to the
        # detector, so it kept emitting clarify and asked the same question four
        # turns running — twice after the customer had already answered it. If we
        # asked last turn, answer with what we have instead.
        if await _last_action_mode(ctx) == "gather":
            logger.info("rebal_clarify_suppressed_after_gather; narrating instead")
            action = RebalanceAction(mode="narrate")
        else:
            # Through the formatter, not raw: the detector's text is a classifier
            # artifact, so returning it directly skipped PI's voice AND wrote no
            # telemetry row — which is what made the loop invisible in the data
            # and left the guard above nothing to read.
            text = await format_relay_or_canned(
                ctx=ctx,
                module_name="rebalancing",
                message=action.clarification_question or _DEFAULT_CLARIFY_FALLBACK,
                action_mode="gather",
            )
            return ChatHandlerResult(
                text=text, snapshot_id=None, rebalancing_recommendation_id=None
            )

    if action.mode == "redirect":
        capture_preference_unserved(
            flow="rebalancing", failure_class="redirect",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
        reason = action.redirect_reason or "change your trades"
        # A "don't sell / lock / keep this fund" ask can't be sent to Profile —
        # answer it honestly instead of the misleading Profile pointer.
        if any(w in reason.lower() for w in ("lock", "keep", "hold", "don't sell", "not sell")):
            message = _LOCK_NOT_SUPPORTED
        else:
            message = _REDIRECT_TEMPLATE.format(reason=reason)
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="rebalancing",
            message=message,
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


def _current_target_mix_pct(response) -> dict[str, float]:
    """Asset-class mix of the recommended plan's targets (tilt baseline)."""
    from app.domains.rebalancing.services.rebal_engine.constraint_impact import (
        _planned_mix_pct,
    )

    return _planned_mix_pct(response)


def _buys_by_fund(response) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in getattr(response, "rows", []) or []:
        name = getattr(r, "recommended_fund", None)
        buy = float(getattr(r, "pass1_buy_amount", 0) or 0)
        if name and buy > 0:
            out[name] = out.get(name, 0.0) + buy
    return out


def _buy_changes_vs_recommended(recommended, requested, *, top: int = 6) -> list[dict]:
    """Per-fund buy change from the recommended plan to the requested plan —
    the DIFFERENCE the tilt makes, which is what the customer wants to see (not
    the requested plan's absolute buys). Biggest moves first, pre-formatted."""
    from common import format_inr_indian  # noqa: E402  (bare import via path hook)

    rec, req = _buys_by_fund(recommended), _buys_by_fund(requested)
    rows = []
    for name in set(rec) | set(req):
        delta = req.get(name, 0.0) - rec.get(name, 0.0)
        if abs(delta) < 1000:  # ignore rounding noise
            continue
        rows.append({
            "fund": name,
            "recommended_indian": format_inr_indian(rec.get(name, 0.0)),
            "requested_indian": format_inr_indian(req.get(name, 0.0)),
            "change_indian": ("+" if delta > 0 else "−") + (format_inr_indian(abs(delta)) or "₹0"),
            "_abs": abs(delta),
        })
    rows.sort(key=lambda x: x.pop("_abs"), reverse=True)
    return rows[:top]


async def _degraded_or_none(ctx: TurnContext, outcome) -> ChatHandlerResult | None:
    """Shared blocking/None-response exits for the two-run preference path."""
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
    return None


async def _handle_preference_counterfactual(
    ctx: TurnContext, action: RebalanceAction
) -> ChatHandlerResult:
    """Two-run comply-and-caution for exposure preferences (spec 2026-08-24).

    Run 1 = recommended plan. Run 2 = requested plan (tilt/scope applied).
    Deviation between the two is the caution lens. Stateless: persist=False.
    Magnitude defaults are policy, recorded in ``applied_preferences``.
    """
    baseline = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        persist=False,
        chat_ctx=ctx,
    )
    early = await _degraded_or_none(ctx, baseline)
    if early is not None:
        return early

    current_mix = _current_target_mix_pct(baseline.response)
    tilt = normalize_tilt(
        current_mix,
        scope_only=action.scope_only_asset_classes,
        tilt_asset_class=action.tilt_asset_class,
        tilt_delta_pp=action.tilt_delta_pp,
        tilt_target_pct=action.tilt_target_pct,
    )

    if tilt.mix_pct is None:
        # No tilt actually expressed — fall through to the plain override path.
        return await _counterfactual_explore(ctx, action.overrides or {})

    overrides = {"asset_class_tilt": tilt.mix_pct}
    applied: dict[str, Any] = {
        "tilt": {"source": "default_step" if tilt.default_step_applied
                 else "customer_number"}
    }
    # Explicit "only/all/100% equity" (mix ≈ 100% equity) → drop hybrid funds so
    # the plan is genuinely all-equity, not ~85% after the look-through.
    if tilt.mix_pct.get("equity", 0.0) >= 99.0:
        overrides["pure_equity_only"] = True
        applied["tilt"]["pure_equity"] = True

    requested_classes = list(action.scope_only_asset_classes or [])
    if action.tilt_asset_class:
        requested_classes.append(action.tilt_asset_class)
    absent = [c for c in requested_classes if current_mix.get(c, 0.0) < 0.5]
    if absent:
        applied["shortfall_note"] = (
            f"no {', '.join(absent)} holdings exist to scale — the request "
            "was spread over the classes present in the plan"
        )

    # Persist the requested (tilted) plan as a CANDIDATE so the customer can Save
    # it. It stays firewalled out of the committed/current reads until saved — a
    # tilt they merely view never becomes their plan (see saved_plan_service).
    requested_run = await compute_rebalancing_result(
        user=ctx.user_ctx,
        user_question=ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        persist=True,
        origin=ORIGIN_CANDIDATE,
        chat_ctx=with_chat_overrides(ctx, overrides),
    )
    early = await _degraded_or_none(ctx, requested_run)
    if early is not None:
        return early

    # LEAN impact: on a tilt turn the caution is requested-vs-RECOMMENDED, never
    # vs the ideal mix. We deliberately do NOT build the consolidate lenses
    # (target/ideal, largest_deviations) — "N points from ideal" made every tilt
    # look catastrophic when the recommended plan is itself far from ideal.
    recommended = {k: round(v, 1) for k, v in current_mix.items()}
    requested = {
        k: round(v, 1)
        for k, v in _current_target_mix_pct(requested_run.response).items()
    }
    impact = {
        "applied_preferences": applied,
        "recommended_mix_pct": recommended,
        "requested_mix_pct": requested,
        "buy_changes_vs_recommended": _buy_changes_vs_recommended(
            baseline.response, requested_run.response
        ),
        "risk_profile": getattr(ctx.user_ctx, "risk_profile", None),
        "tilt_note": (
            "Contrast the requested plan ONLY against the recommended plan — "
            f"recommended is {recommended}, requested is {requested} "
            "(equity/debt/others %); quote these verbatim. Do NOT compare to the "
            "customer's 'ideal' mix or say the plan is 'N points above/below "
            "ideal' — the recommended plan is the baseline, not the ideal, and an "
            "ideal comparison makes every tilt look extreme. State the move as the "
            "gap between these two (e.g. '10 points more equity than we "
            "recommend'). For the funds, show the CHANGE your tilt makes, not the "
            "requested plan's absolute buys: use buy_changes_vs_recommended (each "
            "entry has fund, recommended_indian, requested_indian, change_indian) "
            "— e.g. 'vs our recommendation this puts change_indian into <fund>' — "
            "so the customer sees exactly what moved. Tax belongs to the requested "
            "plan; its sells may differ. Never blend the two mixes."
        ),
    }
    text = await _format_or_fallback_rebal(
        ctx=ctx,
        response=requested_run.response,
        fallback_brief=requested_run.formatted_text or "",
        action_mode="counterfactual_explore",
        # No goal_buckets on a tilt turn: the per-bucket equity splits
        # (e.g. "49% in the medium-term bucket") get mixed with the overall
        # comparison and confuse the customer. The contrast here is the whole-
        # portfolio recommended vs requested mix, carried in constraint_impact.
        goal_buckets=None,
        constraint_impact=impact,
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=None,
        rebalancing_recommendation_id=requested_run.recommendation_id,
    )


async def _counterfactual_explore(
    ctx: TurnContext,
    overrides: dict[str, Any],
) -> ChatHandlerResult:
    """Run engine with overrides, do NOT persist, narrate as hypothetical."""
    if not overrides or not _validate_overrides(overrides):
        capture_preference_unserved(
            flow="rebalancing", failure_class="invalid_override",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
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
    applied_prefs: dict[str, Any] = {}
    allowed: tuple[str, ...] | None = None
    if action.allowed_categories:
        resolved, unresolved = resolve_categories(action.allowed_categories)
        if unresolved and not resolved:
            capture_preference_unserved(
                flow="rebalancing", failure_class="category_unranked",
                session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
            )
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

    excluded: tuple[str, ...] | None = None
    if action.excluded_categories:
        resolved_ex, unresolved_ex = resolve_categories(action.excluded_categories)
        if unresolved_ex and not resolved_ex:
            capture_preference_unserved(
                flow="rebalancing", failure_class="category_unranked",
                session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
            )
            return ChatHandlerResult(
                text=(
                    f"I couldn't match {', '.join(unresolved_ex)} to a fund "
                    "category we invest in, so I haven't excluded anything. "
                    "Which category did you mean?"
                ),
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        excluded = tuple(resolved_ex) if resolved_ex else None
        if excluded:
            applied_prefs["excluded_categories"] = list(excluded)

    weights: dict[str, float] | None = None
    if action.category_weights:
        # Resolve one word at a time so each pct stays attached to its word.
        weights = {}
        weight_default_used = False
        unresolved_w: list[str] = []
        for word, pct in action.category_weights.items():
            canon_list, _ = resolve_categories([word])
            if not canon_list:
                unresolved_w.append(word)
                continue
            canon = canon_list[0]
            if pct and pct > 0:
                weights[canon] = float(pct) / 100.0
            else:
                # Detector's no-number sentinel (0) -> documented default step.
                weights[canon] = _DEFAULT_WEIGHT_STEP
                weight_default_used = True
        if not weights:
            capture_preference_unserved(
                flow="rebalancing", failure_class="category_unranked",
                session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
            )
            return ChatHandlerResult(
                text=(
                    f"I couldn't match {', '.join(unresolved_w)} to a "
                    "fund category we rank, so I haven't reweighted anything. "
                    "Which category did you mean?"
                ),
                snapshot_id=None,
                rebalancing_recommendation_id=None,
            )
        applied_prefs["category_weights_pct"] = {
            k: round(v * 100, 1) for k, v in weights.items()
        }
        if weight_default_used:
            applied_prefs["weight_default_applied"] = True

    constraints = ConsolidationConstraints(
        target_fund_count=action.target_fund_count,
        allowed_categories=allowed,
        excluded_categories=excluded,
        category_weight_targets=weights,
    )

    # Incomplete ask ("fewer funds", no count/category) → ask ONCE. If we already
    # asked last turn and still have no number, do the work with a sensible
    # default rather than asking again: the customer answered in words we
    # couldn't parse ("as few as possible", "you decide"), and repeating the
    # identical sentence leaves them stuck. They can correct it in one word.
    defaulted_fund_count = False
    if not constraints_active(constraints):
        if await _last_action_mode(ctx) == "gather":
            logger.info(
                "consolidate_clarify_suppressed_after_gather; defaulting to %d funds",
                _DEFAULT_CONSOLIDATE_FUND_COUNT,
            )
            constraints = ConsolidationConstraints(
                target_fund_count=_DEFAULT_CONSOLIDATE_FUND_COUNT,
                allowed_categories=allowed,
            )
            defaulted_fund_count = True
        else:
            text = await format_relay_or_canned(
                ctx=ctx,
                module_name="rebalancing",
                message=_CONSOLIDATE_CLARIFY,
                action_mode="gather",
            )
            return ChatHandlerResult(
                text=text,
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
        capture_preference_unserved(
            flow="rebalancing", failure_class="category_not_in_plan",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
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
    if err == "weight_category_not_in_plan":
        capture_preference_unserved(
            flow="rebalancing", failure_class="category_not_in_plan",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
        cats = ", ".join((weights or {}).keys())
        return ChatHandlerResult(
            text=(
                f"Your current plan has no buys in {cats}, so there's no "
                "position there to increase. Want me to show the plan as it "
                "stands, or restrict the new money to that category instead?"
            ),
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    impact = build_constraint_impact(
        outcome.response,
        reshaped,
        risk_profile=getattr(ctx.user_ctx, "risk_profile", None),
    )
    # We chose the number, not the customer — the reply must say so, or they'll
    # read "5 funds" as something they asked for and never correct it.
    if defaulted_fund_count:
        impact["defaulted_fund_count"] = _DEFAULT_CONSOLIDATE_FUND_COUNT
    # Same disclosure duty for a count bumped by protected weight categories.
    if action.target_fund_count is not None:
        actual = getattr(
            getattr(reshaped, "totals", None), "funds_to_buy_count", None
        )
        if actual is not None and actual > action.target_fund_count:
            impact["count_bumped_to"] = actual
    if applied_prefs:
        impact["applied_preferences"] = applied_prefs
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
# Named-fund replies (spec 2026-08-24 — answers from ranking data, no engine run)
# ---------------------------------------------------------------------------


async def _handle_named_fund(
    ctx: TurnContext, action: RebalanceAction
) -> ChatHandlerResult:
    """Answer 'use fund X' / 'why not fund X?' from the ranking CSV.

    Inclusion is deferred to Phase 2 (input-builder seam) — the reply is
    honest about it and the ask is measured. Why-not answers quote the CSV's
    own selection/rejection reasons. Unknown/ambiguous never guesses a fund.
    """
    res = resolve_ranked_fund(action.named_fund or "")
    intent = action.named_fund_intent or "why_not"

    async def _relay(message: str) -> ChatHandlerResult:
        text = await format_relay_or_canned(
            ctx=ctx, module_name="rebalancing", message=message
        )
        return ChatHandlerResult(
            text=text, snapshot_id=None, rebalancing_recommendation_id=None
        )

    if intent == "include":
        capture_preference_unserved(
            flow="rebalancing", failure_class="named_include_deferred",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
        if res.status == "recommended":
            return await _relay(
                f"Swapping a specific fund into the plan from chat isn't "
                f"supported yet — it's coming. For what it's worth, "
                f"{res.fund_name} IS on our recommended list "
                f"({res.sub_category}), so when it lands this will be easy. "
                f"For now the plan stands as computed."
            )
        if res.status == "rejected":
            return await _relay(
                f"Swapping a specific fund into the plan isn't supported from "
                f"chat yet. Also worth knowing: we evaluated {res.fund_name} "
                f"and didn't pick it — {res.rejection_text}"
            )
        return await _relay(
            "Swapping a specific fund into the plan isn't supported from chat "
            "yet — and I couldn't match that name to a fund we rank, so I'd "
            "rather not guess. The current plan stands."
        )

    # why_not
    if res.status == "rejected":
        return await _relay(
            f"We did evaluate {res.fund_name} ({res.sub_category}) and chose "
            f"not to recommend it: {res.rejection_text}"
        )
    if res.status == "recommended":
        row = ranking_by_isin(res.isin) if res.isin else None
        reason = (row.selection_reason if row else "") or "it ranks well in its category"
        return await _relay(
            f"Actually, {res.fund_name} IS on our recommended list "
            f"({res.sub_category}) — {reason}"
        )
    if res.status == "ambiguous":
        options = "; ".join(res.candidates)
        return await _relay(
            f"That name matches more than one fund we track ({options}) — "
            f"which one did you mean?"
        )
    capture_preference_unserved(
        flow="rebalancing", failure_class="fund_unknown",
        session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
    )
    return await _relay(
        "I couldn't match that name to a fund in our ranking universe, so I "
        "can't speak to it honestly — we only comment on funds we've "
        "actually evaluated."
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
