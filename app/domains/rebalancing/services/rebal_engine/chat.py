"""Single chat handler for the REBALANCING intent."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.chat_dispatcher import (
    ChatHandlerResult,
    consume_speculative_detect,
    register,
    register_speculative_detector,
)
from app.domains.ai_engine.common import (
    build_detect_history_block,
    buy_changes_vs_recommended,
    ensure_ai_agents_path,
)
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
from app.domains.rebalancing.services.rebalancing_persist_service import (
    persist_rebalancing_recommendation,
)
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run
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
from app.domains.profile.services import preference_save_service as prefs
from app.domains.profile.services import preference_view as pref_view
from app.domains.profile.services.preference_lexicon import PreferenceAsk, build_intent

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
            "carryforward_lt_loss_inr, additional_cash_inr. Exposure / category "
            "asks go in preference_asks, never here."
        ),
    )
    clarification_question: Optional[str] = Field(default=None)
    redirect_reason: Optional[str] = Field(default=None)
    preference_asks: Optional[list[PreferenceAsk]] = Field(
        default=None,
        description=(
            "Every asset-class or fund-category exposure the customer wants "
            "changed, one entry each: 'more equity' → [{target: equity, level: "
            "more}]; 'small-cap heavy, drop US funds' → [{small_cap, heavy}, "
            "{us_international, none}]; '100% equity' → [{equity, number, 100}]; "
            "'make gold 30%' → [{gold, number, 30}]. Numbers ONLY when the "
            "customer stated one. Set with mode counterfactual_explore."
        ),
    )
    excluded_categories: Optional[list[str]] = Field(
        default=None,
        description=(
            "For consolidate. ONLY the ELSS / lock-in exclusion for NEW buys "
            "('no ELSS', 'nothing with a lock-in' → ['elss']). Every other "
            "category exclusion is a preference_asks entry with level none."
        ),
    )
    category_weights: Optional[dict[str, float]] = Field(
        default=None,
        description=(
            "For consolidate. Weight NEW BUYS toward a category we do not track "
            "as a preference ('at least 30% in banking funds'): {customer's words: "
            "percent 0-100, or 0 when none stated}. Large/mid/small cap, value, "
            "sector, US, multi-asset, debt and gold asks are preference_asks, "
            "NOT this."
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
            "Max number of NEW-BUY funds the customer wants (not the "
            "portfolio's total fund count). May accompany preference_asks."
        ),
    )
    allowed_categories: Optional[list[str]] = Field(
        default=None,
        description=(
            "For consolidate. Where the NEW MONEY of this plan should go, in "
            "the customer's words verbatim, ONLY when they talk about the new "
            "buys ('put the new money only in index funds'). An ask about their "
            "overall exposure is preference_asks."
        ),
    )


_INVALID_OVERRIDE_TEMPLATE = (
    "I can only run 'what if' scenarios on a small set of inputs from chat "
    "right now (tax rate, STCG offset budget, carry-forward losses, additional "
    "cash to deploy — or a preference about your asset-class or fund-category "
    "exposure). Other changes — like "
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
  `overrides` or `preference_asks`. Allowed override keys (others → redirect):
    effective_tax_rate:        number 0-100 (% — overrides customer's tax bracket)
    stcg_offset_budget_inr:    number ≥ 0 (₹ — STCG offset budget for this run)
    carryforward_st_loss_inr:  number ≥ 0 (₹ — short-term carryforward losses)
    carryforward_lt_loss_inr:  number ≥ 0 (₹ — long-term carryforward losses)
    additional_cash_inr:       number ≥ 0 (₹ — relative, "what if I had ₹2L more to deploy?" → 200000; re-runs allocation at corpus + this, then rebalances against present holdings)
  Multiple keys are allowed in one action ("what if my tax rate were 20%
  AND I had ₹50K in carry-forward losses?"). Does NOT persist on this turn.
  PREFERENCE ASKS are ALWAYS counterfactual_explore — any request to change how
  much of an asset class or fund category they hold, even mid-conversation and
  even alongside risk-score talk. Fill `preference_asks`, one entry per thing
  named, with the customer's level: more / heavy / less / none, or number when
  they state a percentage. NEVER instead clarify about their risk score,
  redirect to Profile, or call it a goal/profile conflict. Word map:
    "more equity" / "be aggressive" / "push equity up"   -> [{equity, more}]
    "make it safer" / "more conservative" / "reduce risk" -> [{debt, more}]
    "equity heavy" / "mostly equity"                      -> [{equity, heavy}]
    "only equity" / "all equity" / "100% equity"          -> [{equity, number, 100}]
    "no debt" / "drop debt"                               -> [{debt, none}]
    "take equity to 70%"                                  -> [{equity, number, 70}]
    "add gold" / "more gold"                              -> [{gold, more}]
    "more small cap" / "tilt to smallcaps"                -> [{small_cap, more}]
    "small-cap heavy"                                     -> [{small_cap, heavy}]
    "drop US funds" / "no international"                  -> [{us_international, none}]
    "more value funds"                                    -> [{value, more}]
    "no sectoral / thematic funds"                        -> [{sector, none}]
    "drop the multi-asset funds"                          -> [{multi_asset, none}]
    "more equity and drop US"                             -> [{equity, more}, {us_international, none}]
  A category we don't track as a preference ("banking funds", "ESG", a fund
  house) -> [{other, <level>, other_words: their words}]. A follow-up that
  ADJUSTS a prior what-if is STILL a preference ask ("add gold to that",
  "now make it safer", "a bit more equity than that") — recompute it; NEVER
  reply that you can't adjust the plan on the fly. A fund-count ask alongside
  ("more small cap, max 4 funds") also sets target_fund_count — set ALL fields.
  Two conflicting asset-class asks in one turn ("more equity and more debt")
  -> mode clarify naming the conflict, with preference_asks left UNSET (a
  filled preference_asks always runs). Tax/cash `overrides` may accompany
  preference_asks in the same action.
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
  with what we have. On clarify, leave preference_asks unset.
- "consolidate" — they want FEWER new-buy funds, or the new money restricted
  to / weighted toward / kept out of specific fund categories. This reshapes
  only the BUY side of the plan (sells and tax are untouched). Optional fields:
    category_weights: dict — weight NEW BUYS toward a category we do not track
      as a preference ("at least 30% in banking funds") → {customer's words:
      percent 0-100}; no stated percent → 0 (sentinel — the app applies its
      documented default step and discloses it). Large/mid/small cap, value,
      sector, US, multi-asset, debt sub-types and gold are preference_asks,
      NOT this.
    excluded_categories: list[str] — "no ELSS", "nothing with a lock-in" →
      ["elss"]. "No sectoral funds" is a preference_asks entry {sector, none},
      not this.
  CONTRADICTION: if the same turn excludes a category AND asks for more of it
  (or scopes to an asset class that excludes a requested category — "only debt
  funds but add more mid cap" — mid cap is equity), emit clarify instead,
  naming the conflict in clarification_question.
    target_fund_count: int — "reduce my trades", "fewer funds", "keep it to 5
      funds" → the max number of NEW-BUY funds. If they say a number, set it.
      "exactly N funds for my whole portfolio" is NOT supported, but still emit
      consolidate with target_fund_count=N (the handler adds an honesty note).
    allowed_categories: list[str] — ONLY when the customer talks about where
      the NEW MONEY / new buys of this plan should go ("put the new money
      only in index funds") → their category words verbatim. An ask about
      their overall exposure ("only large cap", "put it all in gold") is
      preference_asks.
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
  supported yet). A category we don't track at all (ESG, a fund house) is a
  preference_asks entry with target other, not redirect. Set `redirect_reason`
  to a short description. (An exposure or category change is NEVER redirect —
  see PREFERENCE ASKS.)
  ONE narrow exception: a request to change the STORED PREFERENCE RECORD itself
  — "remove my small cap preference", "clear my saved preference", "undo the
  preference I set", "reset my preferences" — IS redirect. Set redirect_reason
  to "change your saved preference" (the word "preference" MUST appear) and
  leave preference_asks UNSET. Chat can reshape the plan but cannot write the
  record. Keep this NARROW: an ask about the EXPOSURE is still a preference ask.
  "Remove small caps" -> preference_asks [{small_cap, none}]; "remove my
  small-cap PREFERENCE" -> redirect. One word apart, opposite modes — and
  getting it wrong EXCLUDES the category instead of unsetting it.

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

counterfactual_explore (a value to test, or a preference ask — commit-shaped
"save with…" still counts):
- "what if my tax rate were 20%?"           → overrides={effective_tax_rate: 20}
- "what if I had ₹2L more to deploy?"       → overrides={additional_cash_inr: 200000}
- "tax 20% AND ₹50K short-term losses"      → overrides={effective_tax_rate: 20,
                                              carryforward_st_loss_inr: 50000}
- "increase my equity exposure"             → preference_asks=[{equity, more}]
- "take my equity exposure to 70%"          → preference_asks=[{equity, number, 70}]
- "only equity funds" / "make it 100%
  equity" / "keep 100% equity, I accept
  the risk"                                 → preference_asks=[{equity, number, 100}]
                                              (NOT consolidate; do NOT clarify risk score)
- "just make it safer"                      → preference_asks=[{debt, more}]
- "add a little gold as well"               → preference_asks=[{gold, more}]
- "I want more mid cap in this plan"        → preference_asks=[{mid_cap, more}]
- "make it small-cap heavy"                 → preference_asks=[{small_cap, heavy}]
- "more equity and drop the US funds"       → preference_asks=[{equity, more},
                                              {us_international, none}]
- "only equity, more mid cap, max 4 funds"  → preference_asks=[{equity, number, 100},
                                              {mid_cap, more}], target_fund_count=4
- "only banking funds please"               → preference_asks=[{other, more,
                                              other_words: "banking funds"}]
- "only ESG funds please"                   → preference_asks=[{other, more,
                                              other_words: "ESG funds"}]

- (after we asked "does that make sense?") "yes" → narrate

consolidate (all mode consolidate — fewer buys, or buys restricted/reweighted/
excluded by category):
- "reduce my trades, too many"              → both fields null (handler asks once)
- "consolidate into 5 funds"                → target_fund_count=5
- (we just asked how many) "5"              → target_fund_count=5 (history-fill, don't re-ask)
- "put the new money only in index funds"   → allowed_categories=["index funds"]
- "at least 30% in banking funds"           → category_weights={"banking funds": 30}
- "nothing with a lock-in"                  → excluded_categories=["elss"]

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

  current_asset_class_mix_pct / _indian — {equity, debt, others}: what the
    customer holds TODAY, before any of these trades.
  target_asset_class_mix_pct / _indian — {equity, debt, others}: what they
    will hold AFTER this plan's trades execute. This is the plan's target mix and
    it is the SAME number the Invest page shows on its Current-vs-Target bars.

  ideal_asset_class_mix_pct — {equity, debt, others}: the split their goals and
    risk profile alone call for, ignoring what they currently hold. Present ONLY on
    the first plan-presentation answer; ABSENT on every follow-up. When absent, the
    ideal does not exist for this turn — never mention, quote, or infer one; contrast
    only against target_* (the recommended/practical plan).

  These three are different questions and must never be swapped:
    "what do I hold now?"              → current_*
    "what's the target / what is the
     plan moving me toward?"           → target_*
    "what SHOULD my mix be?"           → ideal_*

  The ideal and the target legitimately differ: the ideal is the destination on
  paper, the target is what THIS plan can reach given what they already hold and
  what it is willing to trade (chiefly, it avoids short-term capital-gains tax by
  selling only long-held units, and keeps holdings still worth owning). ONLY when
  ideal_asset_class_mix_pct is present (the first answer) AND the target sits well
  away from it on equity (more than ~5 points),
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

  A preference is a fact ONLY when active_preferences or constraint_impact says
  so. When neither block is present you cannot attribute this plan to a saved
  preference — do not credit one, and equally do not claim they have none (the
  block is also absent on what-if turns and on older plans). If asked directly,
  say you don't have that on this turn and point them at their preferences.

  NEVER state an asset-class mix or percentage that is not present verbatim in
  CUSTOMER_RECORD. Do not average two mixes, do not interpolate a "middle
  ground", and never invent a compromise split (e.g. "we could trim to
  65/28/7") — if the exact figure is not in the facts, do not give one. Bucket-
  level splits (goal_buckets.planned_split_pct) are PER-BUCKET, never the whole
  portfolio — never present a bucket's equity % as the overall mix.

  Rupee amounts follow the same rule as percentages: state ONLY a ₹ amount that
  appears verbatim as an ``*_indian`` field (a total, a per-bucket / per-fund
  amount, or a group_flows subtotal). NEVER sum, average, or otherwise compute a
  rupee figure of your own — summing several bucket buys into one "group" total in
  prose is exactly what fabricated crore-scale numbers. Need a group total? use
  group_flows; if it isn't there, don't state one.

  buckets: list of one entry per (sub_category) the customer holds or trades.
    Fields per bucket (amounts are pre-formatted _indian strings — cite verbatim):
      sub_category         — SEBI category name, e.g. "Large Cap Fund", "Liquid
                             Fund". THIS is the customer-facing label; copy verbatim.
      current_indian       — present holding in this sub_category
      buy_indian           — amount being bought
      sell_indian          — amount being sold (always non-negative)
      planned_final_indian — current + buy − sell

  group_flows: the customer-facing rollup by group — buckets grouped into a handful
    of labels (e.g. "Multi-asset & hybrid funds", "US & international equity",
    "Small-cap equity", "Debt funds"), largest holding first. Fields per entry:
    group, current_indian, buy_indian, sell_indian, planned_final_indian. This is
    BOTH the plan TABLE (see below) and the pre-computed group subtotal: when you
    describe WHERE money moves at a group level ("into multi-asset funds", "trimming
    small caps"), cite THESE _indian figures verbatim — never add up the underlying
    buckets/funds yourself. A "sell X out of Y held" line MUST take Y from the same
    group's current_indian (never a single fund/category's held).

  warnings: list of short human-readable strings (up to 5)

  fund_house_view: optional — present only on judgement-style rebalancing turns.
    Prozpr's OWN current market stance (our view on large/mid/small-cap equities,
    debt, gold). Use it to FRAME why the plan's direction makes sense ("we're
    cautious on small caps, so the plan trims them"). It is our voice — never name
    or attribute a view to any fund house — and it NEVER overrides the computed
    numbers; the trades stand on their own. Ignore it for purely factual questions.

  fund_actions: per-fund actions — every fund WITH a trade first (never cut), then
    held-as-is rows, capped at 30 (more_holdings_count carries any overflow for
    "and N smaller holdings"). Each: fund_name (customer-facing scheme name, cite
    verbatim), sub_category, and current/buy/sell/planned_final as pre-formatted
    _indian amounts (planned_final = current + buy − sell).
    On any turn that PRESENTS A PLAN (compute, counterfactual_explore, consolidate)
    always include a fund-level trade list: if the plan has FEWER THAN 10 trades
    (see trade_count) show the FULL list — every buy and every sell by fund_name +
    _indian amount; otherwise show the largest ~5 buys and ~5 sells. So the
    customer sees concrete funds, not only categories. For a "what will I hold
    after?" view, list planned_final > 0, biggest first. For narrate/educate, fund
    detail only when the question is fund-specific.
    ALSO on any turn that PRESENTS A PLAN (compute, counterfactual_explore,
    consolidate), you MUST render an actual markdown GROUP table (with | pipes) —
    this is REQUIRED, not optional, and it REPLACES a prose group-by-group
    walkthrough (do not also narrate each group's numbers in a paragraph). Exactly
    THREE numeric columns so it fits a phone: Current | Net change | Final. One row
    per `group_flows` entry (NOT one per SEBI sub_category — that table is too long
    to read), copying the `_indian` amounts verbatim (Current=current_indian, Net
    change=net_change_indian, Final=planned_final_indian), header bold, numbers
    right-aligned, and a bold totals row. Shape:
      | Group | Current | Net change | Final |
      |---|---:|---:|---:|
      | **Multi-asset & hybrid funds** | ₹24.34 lakh | +₹2.42 crore | ₹2.67 crore |
      | ... one row per group_flows entry ... |
      | **Total** | ₹6.29 crore | — | ₹6.29 crore |
    In the totals row COPY total_portfolio_indian for Current AND Final (a rebalance
    preserves the corpus, so Net change is "—" / ₹0 unless direct_stock_sale is
    present) — do NOT re-add the columns yourself. Then the fund-level trade list,
    which gives the specific funds behind the groups. Any commentary about where
    money moves is ONE short lead-in sentence, not a per-group paragraph — the table
    carries the numbers. When a category preference moved a shared subgroup, add ONE
    light line (e.g. "this also nudges your flexi/multi-cap funds in the same
    bucket") — do not imply pin-point precision.

  active_preferences: optional — present when the customer has SAVED investment
    preferences AND this plan was actually shaped by them. NEVER present on the
    same turn as constraint_impact (that block is an unsaved what-if). Fields:
      choices: list[str] — their preference in their own words ("60% equity /
        30% debt / 10% commodity", "no small-cap equity", "30% of your portfolio
        in large-cap equity"). Quote these verbatim; never restate them as engine
        categories, never re-base a percentage, and never add a category or a
        percentage that is not in this list.
      applied: true — the engine consumed the preference on THIS plan.
      shortfall_reason: string|null — present when the engine could not fully
        honour the ask.
    Say "the preferences you saved" — never name WHERE they saved them. They may
    have set this in chat or on a screen, and the pack does not tell you which.

  constraint_impact: optional — on a consolidate OR preference turn. Fields:
      recommended_mix_pct / requested_mix_pct: {equity, debt, others} — the
        recommended plan vs the plan reshaped to the customer's request. On a
        preference turn these two are the ONLY asset-class figures you may cite
        for the contrast — verbatim, never a third number. requested_mix_pct is
        where that plan LANDS (may fall short of a round 100% — give the real
        figure, don't round to what they asked).
      tilt_note: directive string — when present, FOLLOW IT EXACTLY.
      buy_changes_vs_recommended: [{fund, recommended_indian, requested_indian,
        change_indian}] — on a preference turn, the per-fund buy DIFFERENCE from the
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
      applied_preferences: optional dict recording what the preference/count
        actually did. Disclosure keys the reply MUST surface when present:
          fund_count_bumped_to: int — the customer asked for fewer new-buy funds
            than the plan's protected floor allows, so the count was bumped UP to
            this. Own it: "I couldn't go below N funds without dropping a category
            you're invested in."
          not_applied: list[str] — words the customer used that we could not
            turn into a preference; per tilt_note, say so in one sentence.
        customer_choices: the preference as understood, in our internal wire
          format — context only; never quote its keys or "target_pct" verbatim,
          restate it in customer words ("more equity", "no US funds").
      recommended_category_mix_pct / requested_category_mix_pct: {subgroup: % of
        equity} — present on a fund-CATEGORY ask. On those turns the asset-class
        mix barely moves, so lead the contrast with THIS split (per tilt_note),
        naming categories in customer words, citing both verbatim.
      preference_shortfall: string — present when the engine could not fully
        honour the ask (frozen ELSS, an emergency-buffer cut, an oversubscribed
        set of category asks, a category with no holdings). State it in ONE
        plain sentence; never hide it, never dramatise it.
      save_offer: true — this is an UNSAVED what-if. End the reply with one
        sentence telling the customer they can keep this plan, and the
        preference behind it, with the Save plan button below the chat. Never
        frame it as "just this once"; never offer to save it yourself.

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
               When active_preferences is present, ATTRIBUTE the plan in the
               opening: "this plan follows the preferences you saved — 30% of
               your portfolio in large-cap equity, no small-cap equity", quoting
               `choices` verbatim. It is the customer's own instruction being
               honoured: state it as fact, don't thank them for it and don't ask
               whether they still want it. If shortfall_reason is present, add
               ONE plain sentence that the plan could not go all the way, and
               why — never hide it, never dramatise it. This replaces one
               sentence of the budget above; it does not extend it.
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

  On narrate and educate, active_preferences is available but is NEVER the lead:
  use it only when the question touches it ("why is there no small cap?", "is
  this based on what I set?"). A question about tax gets an answer about tax.
  counterfactual_explore — a hypothetical plan the customer ASKED FOR (a
               preference ask: "more equity", "only equity", "small-cap heavy",
               "drop US funds").
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

# Saving or clearing the stored record happens where the customer can SEE it
# before it applies; chat only ever reshapes the plan in view.
_PREFERENCE_CHANGE_TEMPLATE = (
    "I can show you what a change would do to this plan, but saving or clearing "
    "a preference happens in your investment preferences — that way you can see "
    "exactly what's stored before it applies. Open them below and I'll rebuild "
    "the plan from whatever you set."
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

_UNMAPPED_PREFERENCE_TEMPLATE = (
    "I can shape your plan by asset class (equity, debt, gold) and by fund "
    "category — large/mid/small cap, value, sector, US & international, "
    "multi-asset, short-term debt, arbitrage — but not by {words}. Tell me "
    "the category you'd like more or less of."
)
_PREFERENCE_UNCHANGED_TEMPLATE = (
    "That's already your saved preference — the plan you're looking at "
    "reflects it. Say what you'd like to change and I'll show you the "
    "difference."
)
_CONSOLIDATE_CLARIFY = (
    "Happy to consolidate. How many funds would you like the new investments "
    "spread across — for example, up to 3 or up to 5?"
)

# Used when the customer asked to consolidate, we asked how many funds, and their
# reply still carried no number ("as few as possible", "you decide"). Doing the
# work with a stated default beats asking the same question twice.
_DEFAULT_CONSOLIDATE_FUND_COUNT = 5

# "More value funds" with no stated percent -> raise that (non-cap) category to
# this share of the total buy budget (spec 2026-08-24 defaults table; always
# disclosed). Large/mid/small cap asks route to preference_asks, not category_weights.
_DEFAULT_WEIGHT_STEP = 0.10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Sentinel: this turn used the customer's ACTIVE preference. It exists so this
# module never reads the `saved_investment_preference` relationship itself —
# `test_contract_single_computation_reader` reserves that for the profile domain.
_FRESH = object()

_REHYDRATED_MODES = ("narrate", "educate")


async def _preference_row_for_run(ctx: TurnContext, recommendation_id: str):
    """The preference row THIS run was computed under, via the run's own FK.

    Runs in a savepoint for the same reason ``_last_action_mode`` does: a failed
    read would poison the outer session for the rest of the turn.
    """
    from sqlalchemy import select

    from app.domains.rebalancing.models import RebalancingRun

    async with ctx.db.begin_nested():
        stmt = select(RebalancingRun.saved_investment_preference_id).where(
            RebalancingRun.id == uuid.UUID(recommendation_id)
        )
        pref_id = (await ctx.db.execute(stmt)).scalar_one_or_none()
    if pref_id is None:
        return None
    return await prefs.candidate_row(ctx.db, ctx.effective_user_id, pref_id)


async def _preference_row_for_turn(ctx: TurnContext, last_run, action_mode: str):
    """Fresh runs used the ACTIVE preference; a rehydrated plan may predate it,
    so that turn resolves the row the RUN itself points at — otherwise a newly
    saved preference gets credited for an older plan."""
    if action_mode in _REHYDRATED_MODES and last_run is not None and ctx.db is not None:
        raw = ((last_run.output_payload or {}).get("correlation_ids") or {}).get(
            "recommendation_id"
        )
        if not raw:
            return None  # nothing to attribute this plan to
        try:
            return await _preference_row_for_run(ctx, raw)
        except Exception:
            logger.warning("preference row lookup failed", exc_info=True)
            return None
    return _FRESH


async def _format_or_fallback_rebal(
    *,
    ctx: TurnContext,
    response: Any,
    fallback_brief: str,
    action_mode: ActionMode,
    goal_buckets: Optional[list[dict[str, Any]]] = None,
    constraint_impact: Optional[dict[str, Any]] = None,
    is_rerun: bool = False,
    last_run=None,
) -> str:
    """Run the formatter; fall back to the precomputed templated brief on failure."""
    # Prozpr-only house view, gated on the classifier's tools_needed. A rebalance is
    # advice, so when the view is called it frames the trades; the flow sets scope.
    want_view = "fund_house_view" in (getattr(ctx, "tools_needed", ()) or ())
    fund_house_view = load_house_view(prozpr_only=True) if want_view else None
    # Disclose a SAVED preference — but never beside a candidate's own contrast
    # (constraint_impact), and not on a plain tax/cash what-if, which carries no
    # disclosure rule in the prompt.
    if constraint_impact is not None or action_mode == "counterfactual_explore":
        active_preferences = None
    else:
        practical = getattr(response, "practical_allocation", None)
        row = await _preference_row_for_turn(ctx, last_run, action_mode)
        active_preferences = (
            pref_view.active_preferences_for(ctx.user_ctx, practical)
            if row is _FRESH
            else pref_view.active_preferences_block(row, practical)
        )
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_rebal_facts_pack(
            response,
            goal_buckets=goal_buckets,
            constraint_impact=constraint_impact,
            active_preferences=active_preferences,
            is_rerun=is_rerun,
            fund_house_view=fund_house_view,
            # Ship the goal-based ideal ONLY on the plan-presentation (compute) turn,
            # so it reconciles with the allocation tab. Follow-ups/tilts contrast
            # against the recommended plan only — never the ideal (baseline shift).
            include_ideal=(action_mode == "compute"),
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
    return await _detect_rebal_action(last_run, ctx)   # last_run may be None on a first turn (empty-snapshot detect, S2d)


@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    last_run = ctx.last_agent_runs.get("rebalancing")

    # First turn → run engine, format compute output.
    if last_run is None:
        # A first-message preference ask is a what-if on the plan just computed (S2c ruling 3);
        # detect first so only the candidate run is persisted this turn.
        try:
            action = await consume_speculative_detect(ctx)
            if action is None:
                action = await _detect_rebal_action(None, ctx)
        except Exception as exc:
            logger.warning("first-turn detect failed (%s); computing the plain plan", exc)
            action = None
        if action is not None and action.preference_asks:
            return await _handle_preference_what_if(ctx, action, None)

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
    ``handle``)."""
    if action.named_fund:
        return await _handle_named_fund(ctx, action)

    # A request to change the STORED RECORD is not an exposure ask, and this
    # check must sit AHEAD of the preference_asks short-circuit below or it can
    # never fire. Reshaping "remove my small-cap preference" as
    # {small_cap: none} would EXCLUDE small caps — the opposite of the ask.
    if action.mode == "redirect" and "preference" in (
        action.redirect_reason or ""
    ).lower():
        return await _relay(
            ctx, _PREFERENCE_CHANGE_TEMPLATE, show_preferences_pill=True
        )

    # The extracted preference fields are authoritative regardless of the mode
    # label the detector attached (conversational "100% equity" asks sometimes
    # get labelled consolidate / clarify / redirect).
    if action.preference_asks:
        return await _handle_preference_what_if(ctx, action, last_run)

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
            return await _relay(
                ctx,
                action.clarification_question or _DEFAULT_CLARIFY_FALLBACK,
                action_mode="gather",
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
        return await _relay(ctx, message)

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
        # This plan is REHYDRATED and may predate a newer preference, so the
        # disclosure resolves the row off this run rather than the active one.
        last_run=last_run,
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


def _equity_subgroup_mix_pct(response) -> dict[str, float]:
    """Each equity subgroup's share of the plan's post-trade equity holding —
    the contrast for a within-class ask, whose asset-class mix does not move."""
    from practical_asset_allocation.human_override import CLASS_OF  # noqa: E402

    amt: dict[str, float] = {}
    for sg in getattr(response, "subgroups", []) or []:
        name = getattr(sg, "asset_subgroup", None)
        if name and CLASS_OF.get(name) == "equity":
            amt[name] = amt.get(name, 0.0) + float(
                getattr(sg, "suggested_final_holding_inr", 0) or 0
            )
    total = sum(amt.values())
    if total <= 0:
        return {}
    return {k: round(v * 100.0 / total, 1) for k, v in amt.items()}


def _buys_by_fund(response) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in getattr(response, "rows", []) or []:
        name = getattr(r, "recommended_fund", None)
        buy = float(getattr(r, "pass1_buy_amount", 0) or 0)
        if name and buy > 0:
            out[name] = out.get(name, 0.0) + buy
    return out


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


async def _relay(
    ctx: TurnContext,
    message: str,
    action_mode: ActionMode = "redirect",
    show_preferences_pill: bool = False,
) -> ChatHandlerResult:
    text = await format_relay_or_canned(
        ctx=ctx, module_name="rebalancing", message=message, action_mode=action_mode
    )
    return ChatHandlerResult(
        text=text,
        snapshot_id=None,
        rebalancing_recommendation_id=None,
        show_preferences_pill=show_preferences_pill,
    )


def _pending_candidate_id(last_run: AgentRunRecord | None) -> uuid.UUID | None:
    """The candidate preference id the last rebalancing run correlated, if any."""
    payload = getattr(last_run, "output_payload", None) or {}
    raw = (payload.get("correlation_ids") or {}).get("candidate_preference_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


async def _handle_preference_what_if(
    ctx: TurnContext, action: RebalanceAction, last_run: AgentRunRecord | None
) -> ChatHandlerResult:
    """Explore-then-offer (S2): run the ask as a one-off through the S1
    human_override channel, show the reshaped plan against the recommended
    one, then offer to save. The what-if is persisted as a CANDIDATE
    preference row + a candidate rebalancing run FK'd to it; nothing applies
    until the customer says yes."""
    from Rebalancing.consolidation import (  # type: ignore[import-not-found]
        ConsolidationConstraints,
        constraints_active,
        reshape_response,
    )

    # build_intent emits only wire-format-valid output (closed target list,
    # range-checked numbers), so no schema validation is needed here.
    chat_intent, unmapped = build_intent(action.preference_asks)
    if unmapped:
        capture_preference_unserved(
            flow="rebalancing", failure_class="unmapped_category",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
    if not chat_intent:
        return await _relay(
            ctx,
            _UNMAPPED_PREFERENCE_TEMPLATE.format(words=", ".join(unmapped)),
            show_preferences_pill=True,
        )
    if ctx.db is None:
        return ChatHandlerResult(
            text=_NARRATE_DEGRADED_FALLBACK, snapshot_id=None,
            rebalancing_recommendation_id=None,
        )

    # S2 ruling 15: a follow-up ask composes over the candidate the customer is
    # looking at, so "add gold to that" keeps the unsaved "100% equity". Only a
    # LIVE candidate (never activated) qualifies; anything else merges over the
    # saved row.
    base_row = None
    cid = _pending_candidate_id(last_run)
    if cid is not None:
        row = await prefs.candidate_row(ctx.db, ctx.effective_user_id, cid)
        if row is not None and not row.is_active and row.activated_at is None:
            base_row = row

    intent, resolved, changed = await prefs.resolve_one_off(
        ctx.db, ctx.user_ctx, chat_intent, base_row=base_row
    )
    if not changed:
        return await _relay(ctx, _PREFERENCE_UNCHANGED_TEMPLATE, action_mode="compute")

    baseline = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=False, chat_ctx=ctx,
    )
    early = await _degraded_or_none(ctx, baseline)
    if early is not None:
        return early

    overrides: dict[str, Any] = {
        k: v for k, v in (action.overrides or {}).items()
        if k in _REBAL_ALLOWED_OVERRIDE_KEYS
    }
    overrides["human_override_preferences"] = prefs.one_off_override(resolved)
    requested_run = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=False,
        force_fresh_allocation=("additional_cash_inr" in overrides),
        chat_ctx=with_chat_overrides(ctx, overrides),
    )
    early = await _degraded_or_none(ctx, requested_run)
    if early is not None:
        return early

    requested_response = requested_run.response
    applied: dict[str, Any] = {"customer_choices": intent}
    if unmapped:
        applied["not_applied"] = unmapped
    count_c = ConsolidationConstraints(target_fund_count=action.target_fund_count)
    if constraints_active(count_c):
        reshaped, err = reshape_response(requested_response, count_c)
        if err is None:
            requested_response = reshaped
            applied["fund_count"] = action.target_fund_count
            bumped = getattr(getattr(reshaped, "totals", None), "funds_to_buy_count", None)
            if action.target_fund_count and bumped and bumped > action.target_fund_count:
                applied["fund_count_bumped_to"] = bumped

    practical = getattr(requested_response, "practical_allocation", None)
    override_applied = getattr(practical, "human_override_applied", None)
    # The achieved mix is the run's own class breakdown, not a field the
    # engine carries (spec §6).
    achieved = prefs.achieved_class_mix(practical)
    shortfall = getattr(override_applied, "shortfall_reason", None)

    # Candidate row first (the run FKs it), then the candidate run, then the
    # module-run row the next what-if turn reads the candidate id from.
    candidate = await prefs.insert_candidate(ctx.db, ctx.user_ctx, intent, resolved, achieved, shortfall)
    rec_id = await persist_rebalancing_recommendation(
        ctx.db, ctx.effective_user_id, requested_response,
        source_allocation_run_id=requested_run.source_allocation_id,
        chat_session_id=ctx.session_id,
        used_cached_allocation=requested_run.used_cached_allocation,
        user_question=ctx.user_question,
        origin=ORIGIN_CANDIDATE,
        saved_investment_preference_id=candidate.id,
    )
    try:
        await record_ai_module_run(
            ctx.db,
            user_id=ctx.effective_user_id, session_id=ctx.session_id,
            module="rebalancing", reason="preference_what_if",
            intent_detected="rebalancing", spine_mode=None, input_payload=None,
            output_payload={
                "rebalancing_response": requested_response.model_dump(mode="json"),
                "goal_buckets": requested_run.goal_buckets,
                "correlation_ids": {
                    "recommendation_id": str(rec_id),
                    "source_allocation_id": (
                        str(requested_run.source_allocation_id)
                        if requested_run.source_allocation_id else None
                    ),
                    "candidate_preference_id": str(candidate.id),
                },
            },
            emit_standard_log=False,
        )
    except Exception as exc:
        logger.warning("rebal_what_if_ai_module_telemetry skipped (non-fatal): %s", exc)

    recommended = {k: round(v, 1) for k, v in _current_target_mix_pct(baseline.response).items()}
    requested = {k: round(v, 1) for k, v in _current_target_mix_pct(requested_response).items()}
    impact: dict[str, Any] = {
        "applied_preferences": applied,
        "recommended_mix_pct": recommended,
        "requested_mix_pct": requested,
        "buy_changes_vs_recommended": buy_changes_vs_recommended(
            _buys_by_fund(baseline.response),
            _buys_by_fund(requested_response),
            noise_inr=1000,
        ),
        "save_offer": True,
        "tilt_note": (
            "Contrast the requested plan ONLY against the recommended plan — "
            f"recommended is {recommended}, requested is {requested} "
            "(equity/debt/others %); quote these verbatim. Do NOT compare to the "
            "customer's 'ideal' mix. State the move as the gap between these two. "
            "For the funds, show the CHANGE this makes vs the recommended plan "
            "using buy_changes_vs_recommended (fund, recommended_indian, "
            "requested_indian, change_indian). Tax belongs to the requested plan. "
            "Never blend the two mixes."
        ),
    }
    if shortfall:
        impact["preference_shortfall"] = shortfall
    # Only an EQUITY-class category ask leaves the asset-class mix flat. A gold /
    # short_debt / arbitrage ask moves the class mix itself, and the equity-only
    # split below would answer the wrong question.
    from practical_asset_allocation.human_override import CLASS_OF  # noqa: E402

    equity_ask = any(
        CLASS_OF.get(sg) == "equity" for sg in (changed.get("subgroups") or {})
    )
    rec_cat = _equity_subgroup_mix_pct(baseline.response) if equity_ask else {}
    req_cat = _equity_subgroup_mix_pct(requested_response) if equity_ask else {}
    if rec_cat and req_cat:
        impact["recommended_category_mix_pct"] = rec_cat
        impact["requested_category_mix_pct"] = req_cat
        impact["tilt_note"] += (
            " This ask is about fund CATEGORIES: the asset-class mix barely "
            "moves, so do NOT lead with it. Lead the contrast with the category "
            f"split of equity — recommended {rec_cat}, requested {req_cat} "
            "(each category's % of equity); quote these verbatim, naming the "
            "categories in customer words (large/mid/small cap, value, sector, "
            "US & international, multi-asset)."
        )

    try:
        requested_brief = build_fallback_rebal_brief(
            requested_response, used_cached_allocation=False
        )
    except (AttributeError, TypeError, ValueError):
        requested_brief = _NARRATE_DEGRADED_FALLBACK
    text = await _format_or_fallback_rebal(
        ctx=ctx, response=requested_response, fallback_brief=requested_brief,
        action_mode="counterfactual_explore", goal_buckets=None,
        constraint_impact=impact,
    )
    return ChatHandlerResult(
        text=text, snapshot_id=None, rebalancing_recommendation_id=rec_id,
        has_candidate_preference=True,
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
        return await _relay(ctx, _INVALID_OVERRIDE_TEMPLATE)

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
        constraint_impact=None,
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
            return await _relay(ctx, _CONSOLIDATE_CLARIFY, action_mode="gather")

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

    if intent == "include":
        capture_preference_unserved(
            flow="rebalancing", failure_class="named_include_deferred",
            session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
        )
        if res.status == "recommended":
            return await _relay(
                ctx,
                f"Swapping a specific fund into the plan from chat isn't "
                f"supported yet — it's coming. For what it's worth, "
                f"{res.fund_name} IS on our recommended list "
                f"({res.sub_category}), so when it lands this will be easy. "
                f"For now the plan stands as computed."
            )
        if res.status == "rejected":
            return await _relay(
                ctx,
                f"Swapping a specific fund into the plan isn't supported from "
                f"chat yet. Also worth knowing: we evaluated {res.fund_name} "
                f"and didn't pick it — {res.rejection_text}"
            )
        return await _relay(
            ctx,
            "Swapping a specific fund into the plan isn't supported from chat "
            "yet — and I couldn't match that name to a fund we rank, so I'd "
            "rather not guess. The current plan stands."
        )

    # why_not
    if res.status == "rejected":
        return await _relay(
            ctx,
            f"We did evaluate {res.fund_name} ({res.sub_category}) and chose "
            f"not to recommend it: {res.rejection_text}"
        )
    if res.status == "recommended":
        row = ranking_by_isin(res.isin) if res.isin else None
        reason = (row.selection_reason if row else "") or "it ranks well in its category"
        return await _relay(
            ctx,
            f"Actually, {res.fund_name} IS on our recommended list "
            f"({res.sub_category}) — {reason}"
        )
    if res.status == "ambiguous":
        options = "; ".join(res.candidates)
        return await _relay(
            ctx,
            f"That name matches more than one fund we track ({options}) — "
            f"which one did you mean?"
        )
    capture_preference_unserved(
        flow="rebalancing", failure_class="fund_unknown",
        session_id=ctx.session_id, distinct_id=ctx.effective_user_id,
    )
    return await _relay(
        ctx,
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
        "has_sells": any((fa.get("sell_indian") or "₹0") != "₹0" for fa in fund_actions),
        "sub_categories": list(
            dict.fromkeys(b.get("sub_category") for b in buckets if b.get("sub_category"))
        ),
        "fund_names": list(
            dict.fromkeys(fa.get("fund_name") for fa in fund_actions if fa.get("fund_name"))
        ),
    }


async def _detect_rebal_action(
    last_run: AgentRunRecord | None,
    ctx: TurnContext,
) -> RebalanceAction:
    """One Haiku call returning a RebalanceAction. Uses the shared classify_action.
    ``last_run`` is None on a first turn (no snapshot yet)."""
    slim = _slim_snapshot(last_run.output_payload if last_run else None)
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
