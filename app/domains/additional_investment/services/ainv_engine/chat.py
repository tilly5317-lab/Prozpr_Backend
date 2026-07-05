"""Single chat handler for the ADDITIONAL_INVESTMENT intent.

BUY-only / write-once flow: on every turn of this intent the handler parses the
deploy amount + cadence from the question, runs the compute orchestrator, builds
a facts pack that NAMES the funds to buy, and formats the answer through the
SHARED question-aware formatter. There is no follow-up classifier or
counterfactual/override machinery here — additional-investment runs are
write-once, so each turn simply recomputes the BUY list in `compute` mode. When
the question carries no amount the handler asks for it; when the orchestrator
returns a blocking_message it relays that instead. Peer of rebal_engine/chat.py,
minus the follow-up branches.

Not re-exported from ainv_engine/__init__.py (circular-import risk via
turn_context); imported lazily by the module-service to trigger @register.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.classifier_llm import classify_action
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.ai_engine.answer_formatter import (
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.common import (
    build_history_block,
    ensure_ai_agents_path,
    format_inr_indian,
)
from app.domains.additional_investment.services.ainv_engine.service import (
    compute_additional_investment_result,
)
from app.domains.additional_investment.services.ainv_engine.category import (
    category_status,
    category_subgroup,
    resolve_category,
    top_funds_for_category,
)
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    _EXCLUDE_SUBGROUPS,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deploy-amount + cadence parser (deterministic; no LLM)
# ---------------------------------------------------------------------------

# Indian money shorthand multipliers (lower-cased suffix -> factor).
_UNIT_MULTIPLIER = {
    "k": 1_000.0,
    "thousand": 1_000.0,
    "l": 100_000.0,
    "lac": 100_000.0,
    "lacs": 100_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "cr": 10_000_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
}

# A number (optional currency prefix, optional thousands separators / decimal)
# followed by an optional Indian unit suffix.
_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)?\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:(crores?|cr|lakhs?|lacs?|l|thousand|k)(?![a-z]))?",
    re.IGNORECASE,
)

# Recurring / monthly phrasing selects a monthly SIP.
_SIP_RE = re.compile(
    r"\b(?:sip|monthly|per ?month|every month|each month)\b",
    re.IGNORECASE,
)


def parse_deploy_request(question: str) -> tuple[float | None, Cadence]:
    """Pull the deploy amount (INR float) and cadence from a free-text question.

    Deterministic, no LLM. The amount understands Indian money shorthand —
    thousands separators and the k/thousand, l/lac/lakh, cr/crore suffixes
    (e.g. "₹5L" -> 500000.0, "50k" -> 50000.0, "Rs 2,00,000" -> 200000.0,
    "invest 75000" -> 75000.0). Returns ``(None, cadence)`` when no amount is
    present. Cadence is ``Cadence.SIP_MONTHLY`` when the text reads like a
    recurring/monthly plan (or contains "/month"), else ``Cadence.LUMPSUM``.
    """
    cadence = (
        Cadence.SIP_MONTHLY
        if (_SIP_RE.search(question) or "/month" in question.lower())
        else Cadence.LUMPSUM
    )

    match = _AMOUNT_RE.search(question)
    if match is None:
        return None, cadence
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None, cadence
    value *= _UNIT_MULTIPLIER.get((match.group(2) or "").lower(), 1.0)
    return value, cadence


# ---------------------------------------------------------------------------
# LLM deploy-request extraction (Haiku; mirrors rebal_engine._detect_rebal_action)
# ---------------------------------------------------------------------------


class _DeployRequest(BaseModel):
    """Structured extraction of the deploy amount, cadence, and optional focus category from the question (+ recent history)."""

    amount_inr: Optional[float] = Field(
        default=None,
        description=(
            "Total rupee amount the customer wants to deploy, as a plain number. "
            "Resolve Indian shorthand: '5L'/'5 lakh' -> 500000, '50k' -> 50000, "
            "'2 crore' -> 20000000, 'Rs 2,00,000' -> 200000. For a monthly SIP this "
            "is the PER-MONTH amount. Null if the question names no amount."
        ),
    )
    cadence: Literal["lumpsum", "sip_monthly"] = Field(
        default="lumpsum",
        description=(
            "sip_monthly when the text reads recurring/monthly (SIP, per month, "
            "every month, /month); otherwise lumpsum (a single one-time deployment)."
        ),
    )
    focus_category: Optional[str] = Field(
        default=None,
        description=(
            "The fund CATEGORY the customer is asking to invest in, verbatim-ish "
            "(e.g. 'smallcap', 'gold', 'ELSS'). Set ONLY when the customer is "
            "asking for that category for this money — a passing mention ('I sold "
            "my smallcap fund') is NOT a request. One category only: when several "
            "are named, pick the dominant one. Null when no category is asked."
        ),
    )


_DEPLOY_EXTRACT_SYSTEM = """You extract three fields from a customer's request to
invest fresh money: the rupee AMOUNT, whether it is a one-time LUMPSUM or a
monthly SIP, and the fund CATEGORY they are asking for (if any). Indian money
shorthand: k/thousand = x1,000; l/lac/lakh = x100,000; cr/crore = x10,000,000.
Ignore numbers that are durations, counts, or years (e.g. "5 years", "3 funds",
"in 2027") — only the money amount goes in amount_inr. A bare "monthly"
describing salary/income/expenses is NOT a SIP; only a recurring INVESTMENT
plan is.

A Recent Conversation History block may precede the current request. Fields the
CURRENT request omits may be filled from history, with two hard rules:
1. The CURRENT request always wins on conflict.
2. A historical amount qualifies ONLY if the customer stated it as money they
   want to invest/deploy. Salary, income, expenses, goal targets, and
   hypothetical/what-if figures NEVER qualify. When in doubt, amount_inr=null.

Examples (H: = earlier history, C: = current request):
- C: "invest 5L as a lumpsum"                    -> 500000, lumpsum, null
- C: "start a 25k monthly SIP in smallcap"       -> 25000, sip_monthly, "smallcap"
- C: "which gold fund should I buy?"             -> null, lumpsum, "gold"
- C: "I sold my smallcap fund, invest 2L"        -> 200000, lumpsum, null
- H: "I want to invest 5 lakhs" C: "smallcap funds only"
                                                 -> 500000, lumpsum, "smallcap"
- H: "my salary is 2L a month" C: "smallcap funds only"
                                                 -> null, lumpsum, "smallcap"
- H: "I want to invest 5 lakhs" C: "make it 2L, ELSS"
                                                 -> 200000, lumpsum, "ELSS"
"""


async def extract_deploy_request(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[float | None, Cadence, str | None]:
    """LLM extraction of (deploy amount INR, cadence, raw category) from free text.

    History-aware (spec 2026-07-04): the last 6 turns ride in the user block so a
    category-only follow-up reuses an amount the customer already stated — but
    only invest-intent amounts qualify (prompt rule; doubt → null → the polite
    re-ask). Falls back to the deterministic regex ``parse_deploy_request``
    (current message only, never a category) when the Haiku call fails.
    """
    history_block = build_history_block(history)
    user_block = (
        (history_block + "\n\n" if history_block else "")
        + f"Customer's current request: {question}"
    )
    try:
        result = await classify_action(
            action_model=_DeployRequest,
            system_prompt=_DEPLOY_EXTRACT_SYSTEM,
            user_block=user_block,
            api_key=get_settings().get_anthropic_additional_investment_key(),
            max_tokens=200,
        )
    except Exception as exc:  # broad, mirroring _detect_rebal_action's call site
        logger.warning("extract_deploy_request failed (%s); using regex fallback", exc)
        amount, cadence = parse_deploy_request(question)
        return amount, cadence, None

    raw_category = (result.focus_category or "").strip() or None
    return result.amount_inr, Cadence(result.cadence), raw_category


# ---------------------------------------------------------------------------
# Formatter body prompt (mirrors _REBAL_FORMATTER_BODY: documents the
# FACTS_PACK shape + per-ActionMode lead/length budget; no narrative prose)
# ---------------------------------------------------------------------------

_AINV_FORMATTER_BODY = """You are answering a customer's question about a fresh
additional-investment recommendation — NEW money being deployed as a monthly
SIP into specific funds. This is BUY-only: nothing is ever sold. The shared
house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  deploy_amount_inr / deploy_amount_indian — the PER-MONTH fresh money being
           deployed.
  cadence: always "sip_monthly" on this surface — the same plan repeats every
           month; frame amounts per-month (use each buy's
           monthly_amount_indian).
  target_bucket: "short_term", "medium_term", or "long_term" — the horizon the
           deploy amount was weighted toward, i.e. the customer's NEAREST UNFUNDED
           goal. "short_term" means a goal under ~3 years is still unfunded, so the
           money leans toward short-term subgroups; "medium_term" means short-term
           is covered but a ~3-6 year goal is unfunded; "long_term" means the short
           and medium goals are funded (or there are none) so the money builds the
           long-term subgroups. This is engine context — explain the WHY in plain
           English; never surface the raw label.
  undeployed_inr / undeployed_indian — money that could NOT be placed (per-fund
           caps bound, or a subgroup lacked eligible funds). 0 when fully placed.
  under_deploy_note — present only when a MATERIAL amount couldn't be deployed
           (trivial ₹100-rounding remainders are dropped) or when NOTHING could be
           deployed at all. Surface its point when present; if buys is empty,
           nothing could be placed — say so plainly and do NOT invent any fund.

  per_subgroup_target: list, one entry per subgroup the deploy was split into:
      subgroup    — internal engine grouping (e.g. "low_beta_equities"). DO NOT
                    surface this raw label; it is context only.
      ratio       — this subgroup's renormalised share of the deploy (0-1).
      target_inr  — the rupee amount targeted at this subgroup.

  buys: list of the specific funds to BUY — this is the substance of the answer:
      recommended_fund — the customer-facing scheme name (e.g. "HDFC Top 100").
                    Cite this VERBATIM; naming the funds is the point of the reply.
      sub_category  — SEBI category for context (e.g. "Large Cap Fund").
      monthly_amount_inr / monthly_amount_indian — the per-month amount to put
                    into this fund. Cite this pair.

When FACTS_PACK contains `category_ask`, the customer asked for a specific fund
category — address it EXPLICITLY (never ignore it):
  asked_text / category — their words / our canonical category (null = we don't
           rank funds there: say so plainly, do NOT invent any).
  top_funds — our top-ranked funds in that category; name them VERBATIM.
  status — where the category stands in THIS plan; narrate accordingly:
    in_plan                      — the plan already buys it: point at that buy.
    subgroup_funded_other_funds  — its part of the portfolio received money via
                                   other funds; name the category picks for a
                                   category-specific tilt.
    at_or_above_ideal            — they already hold at/above their ideal there
                                   (subgroup_current_inr vs subgroup_ideal_inr):
                                   say this deployment adds none, name the top
                                   picks anyway, and caution against
                                   overweighting further.
    excluded_by_policy           — we never deploy fresh chat money there (e.g.
                                   ELSS 3-year lock-in): name the picks, state
                                   the policy.
    plan_by_goals                — (SIP) the plan deploys by goals; name the
                                   category picks alongside.
  ALWAYS close the category topic with the caveat: concentrating in one
  category is not what we'd recommend — the plan spreads by their goals.
  When `buys` exist, the deployment plan is STILL the substance of the reply —
  lead with the headline and NAME the buys as usual; the category discussion
  supplements the plan, never replaces it. NEVER offer to execute a
  category-only deployment (no "want to go full smallcap?", no "how would you
  like to split among these funds?") and never suggest routing an excluded
  category via SIP — the platform does neither. The plan is the
  recommendation; category picks are information.

ACTION_MODE tells you the situation. ACTION_MODE is `compute` here — it is set
by the system on a fresh first-turn recommendation (it is not produced by a
classifier). Per-mode behavior:

  compute    — first-time additional-investment recommendation; introduce it
               shaped by the customer's question. Lead with the headline
               (deploy_amount_indian, per-month), then NAME the 1-3 biggest
               buys with their monthly_amount_indian, and give one
               plain-English line on why the split leans the way it does
               (derived from target_bucket). Name at least the largest fund(s)
               when there are any — the customer asked where their money is
               going; if buys is empty, nothing could be deployed right now, so
               relay the under_deploy_note plainly and do NOT fabricate funds.
               When under_deploy_note is present, close with it. Length: 6-10
               sentences (fewer when there is a single buy).
"""


_AINV_DEFICIT_FORMATTER_BODY = """You are answering a customer's question about
deploying fresh money — a one-time amount being invested into specific funds.
This is BUY-only: nothing is ever sold. The shared house-style rules above apply.

HOW THE RECOMMENDATION WAS BUILT (context for your narrative): we computed the
customer's ideal portfolio for their goals INCLUDING this new money, compared it
with what they currently hold in each part of the portfolio, and directed the
fresh money into the gaps — the parts furthest below their ideal. Explain it in
this plain spirit: "we looked at where your portfolio is versus where it should
be, and this money fills those gaps."

The FACTS_PACK has this shape (treat fields not present as unknown):

  deploy_amount_inr / deploy_amount_indian — total fresh money being deployed
           (one-time; cadence is always a one-time deployment on this surface).
  target_bucket: "short_term" | "medium_term" | "long_term" — which horizon
           received the MOST of this money (derived from where it actually
           went). Context only — never surface the raw label.
  deficit_rows: list, one entry per subgroup the money went into:
      subgroup    — internal engine grouping. DO NOT surface this raw label.
      ideal_inr   — what the customer's ideal says this part should hold.
      current_inr — what they hold there today.
      gap_inr     — the shortfall this deploy is filling.
      buy_inr     — how much of the fresh money goes here.
  undeployed_inr / undeployed_indian — money that could NOT be placed. 0 when
           fully placed.
  under_deploy_note — present only when a MATERIAL amount couldn't be deployed.
           Surface its point when present; if buys is empty say so plainly and
           do NOT invent any fund.
  per_subgroup_target — the rupee split of the deploy (subgroup labels are
           context only). Its `ratio` is the share of THIS deployment — never
           describe it as the customer's overall or ideal portfolio allocation
           ("30% of this money", NOT "your plan calls for 30% in X").
  buys: list of the specific funds to BUY — the substance of the answer:
      recommended_fund — customer-facing scheme name. Cite VERBATIM; naming the
                    funds is the point of the reply.
      sub_category  — SEBI category for context (e.g. "Large Cap Fund").
      amount_inr / amount_indian — the one-time amount for this fund.

When FACTS_PACK contains `category_ask`, the customer asked for a specific fund
category — address it EXPLICITLY (never ignore it):
  asked_text / category — their words / our canonical category (null = we don't
           rank funds there: say so plainly, do NOT invent any).
  top_funds — our top-ranked funds in that category; name them VERBATIM.
  status — where the category stands in THIS plan; narrate accordingly:
    in_plan                      — the plan already buys it: point at that buy.
    subgroup_funded_other_funds  — its part of the portfolio received money via
                                   other funds; name the category picks for a
                                   category-specific tilt.
    at_or_above_ideal            — they already hold at/above their ideal there
                                   (subgroup_current_inr vs subgroup_ideal_inr):
                                   say this deployment adds none, name the top
                                   picks anyway, and caution against
                                   overweighting further.
    excluded_by_policy           — we never deploy fresh chat money there (e.g.
                                   ELSS 3-year lock-in): name the picks, state
                                   the policy.
    plan_by_goals                — (SIP) the plan deploys by goals; name the
                                   category picks alongside.
  ALWAYS close the category topic with the caveat: concentrating in one
  category is not what we'd recommend — the plan spreads by their goals.
  When `buys` exist, the deployment plan is STILL the substance of the reply —
  lead with the headline and NAME the buys as usual; the category discussion
  supplements the plan, never replaces it. NEVER offer to execute a
  category-only deployment (no "want to go full smallcap?", no "how would you
  like to split among these funds?") and never suggest routing an excluded
  category via SIP — the platform does neither. The plan is the
  recommendation; category picks are information.

ACTION_MODE is `compute` — a fresh recommendation. Lead with the headline
(deploy_amount_indian, one-time), then NAME the 1-3 biggest buys with their
amounts, and give one plain-English line on why the money went where it did —
the gap-fill story, not a horizon label. Any percentage you cite from the split
is a share of this deployment, never the plan's overall target mix. When part of the deployment lands in
emergency/liquid funds, say so plainly ("part of this builds your emergency
cushion — the foundation; the rest goes to your growth gaps") rather than
leaving a liquid-fund buy unexplained. When under_deploy_note is present, close
with it. Length: 6-10 sentences (fewer when there is a single buy).
"""


_AINV_CATEGORY_PROBE_BODY = """The customer asked which funds to buy in a specific
CATEGORY but has not yet said how much they want to invest. Answer the literal
question honestly, then ask for the amount. The shared house-style rules above
apply.

FACTS_PACK has a single field, `category_ask`:
  asked_text  — the category in the customer's words (e.g. "smallcap").
  category    — our canonical name for it, or null when we don't rank funds
                in that category.
  status      — "not_ranked" when category is null; otherwise ignore here.
  top_funds   — our top-ranked funds in the category (fund + sub_category).

Reply shape:
1. Name the top_funds VERBATIM as our highest-ranked picks in that category —
   naming them is the point. When top_funds is empty (not_ranked), say plainly
   that we don't have ranked funds in that category and do NOT invent any.
2. ALWAYS the caveat, in PI's voice: putting everything into one category isn't
   what we'd recommend — our plan spreads fresh money across the gaps in their
   goal-based portfolio.
3. Ask how much they want to invest and whether one-time lumpsum or monthly
   SIP — once known, we'll show exactly where the money goes (including their
   category, when it fits).
Length: 3-5 sentences, warm. Do NOT fabricate amounts, returns, or rankings.
Do NOT offer to execute a category-only deployment or ask how they'd split
money among the category funds — once the amount is known, the goal-based plan
decides placement (their category included only where it fits).
"""


# ---------------------------------------------------------------------------
# Facts pack
# ---------------------------------------------------------------------------


def _under_deploy_note(
    deploy_inr: float, undeployed_inr: float, n_targets: int, has_buys: bool
) -> str | None:
    """Customer-facing note about money that couldn't be deployed — or None.

    Suppresses the trivial ₹100-rounding remainder a multi-subgroup split almost
    always leaves (under ~₹100 per subgroup, or 0.5% of the deploy). Returns a
    DISTINCT message when NOTHING could be placed (the targeted horizon bucket had
    no eligible funds) versus a genuine per-fund-cap / fund-scarcity shortfall.
    """
    if undeployed_inr <= 0:
        return None
    if not has_buys:
        return (
            f"I couldn't place any of your {format_inr_indian(deploy_inr)} right now — "
            "there are no eligible funds to add to in the bucket this deploy targets. "
            "Your emergency reserve is always kept out of fresh deployment."
        )
    threshold = max(100.0 * max(n_targets, 1), 0.005 * deploy_inr)
    if undeployed_inr <= threshold:
        return None
    return (
        f"{format_inr_indian(undeployed_inr)} of your {format_inr_indian(deploy_inr)} "
        "couldn't be placed — per-fund caps or a shortage of eligible funds left a "
        "remainder. Your emergency reserve is always kept out of fresh deployment."
    )


def build_ainv_facts_pack(
    output: AdditionalInvestmentOutput,
    deficit_rows: list[dict[str, Any]] | None = None,
    category_ask: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Curated facts the formatter LLM may cite. Customer-tellable only — no ISIN.

    Flat dict: deploy accounting, cadence/target-bucket context, the per-subgroup
    targets, and the BUY list with each fund named. Money is cast to float and
    formatted with format_inr_indian (the allocation family is float, not
    Decimal). When undeployed_inr > 0 an `under_deploy_note` one-liner is added
    (O6: the emergency reserve is excluded and caps / fund-scarcity can leave a
    remainder).
    """
    deploy_inr = float(output.deploy_amount_inr)
    undeployed_inr = float(output.undeployed_inr)

    buys: list[dict[str, Any]] = []
    for b in output.buys:
        monthly_inr = (
            float(b.monthly_amount_inr) if b.monthly_amount_inr is not None else None
        )
        # Exactly one figure is meaningful per buy: a SIP buy carries only the
        # per-month amount (amount_inr == monthly here, so don't double-surface it),
        # a lumpsum buy carries only the one-time amount.
        amount_inr = None if monthly_inr is not None else float(b.amount_inr)
        buys.append(
            {
                "recommended_fund": b.recommended_fund,
                "sub_category": b.sub_category,
                "amount_inr": amount_inr,
                "amount_indian": (
                    format_inr_indian(amount_inr) if amount_inr is not None else None
                ),
                "monthly_amount_inr": monthly_inr,
                "monthly_amount_indian": (
                    format_inr_indian(monthly_inr) if monthly_inr is not None else None
                ),
            }
        )

    per_subgroup_target = [
        {
            "subgroup": t.subgroup,
            "ratio": float(t.ratio),
            "target_inr": float(t.target_inr),
        }
        for t in output.per_subgroup_target
    ]

    facts: dict[str, Any] = {
        "deploy_amount_inr": deploy_inr,
        "deploy_amount_indian": format_inr_indian(deploy_inr),
        "cadence": output.cadence.value,
        "target_bucket": output.target_bucket.value,
        "buys": buys,
        "per_subgroup_target": per_subgroup_target,
        "undeployed_inr": undeployed_inr,
        "undeployed_indian": format_inr_indian(undeployed_inr),
    }
    note = _under_deploy_note(
        deploy_inr, undeployed_inr, len(per_subgroup_target), bool(output.buys)
    )
    if note is not None:
        facts["under_deploy_note"] = note
    if deficit_rows is not None:
        # Deficit-fill context (lumpsum): ideal-vs-current per subgroup, so the
        # formatter can narrate WHERE the gaps were. Engine labels — context
        # only, never surfaced raw.
        facts["deficit_rows"] = deficit_rows
    if category_ask is not None:
        # The customer asked for a specific category — top-ranked picks + where
        # that category stands in this plan (spec 2026-07-04). Status vocabulary
        # is contractual; the body prompt narrates per-status.
        facts["category_ask"] = category_ask
    return facts


# ---------------------------------------------------------------------------
# Deterministic fallback (used when the formatter LLM call fails)
# ---------------------------------------------------------------------------


def _fallback_category_line(category_ask: dict[str, Any]) -> str:
    """Deterministic one-liner so the fallback path never ignores the asked
    category (the original bug). Names the top picks + the standing caveat."""
    asked = category_ask.get("asked_text") or "that category"
    funds = [f["fund"] for f in category_ask.get("top_funds") or []]
    if not funds:
        return (
            f"_On {asked} specifically: we don't have ranked funds in that "
            "category — the plan above follows your goals instead._"
        )
    return (
        f"_On {asked} specifically: our top-ranked picks are "
        f"{', '.join(funds)} — but we recommend the plan above "
        "over concentrating in one category._"
    )


def _build_fallback_category_probe(category_ask: dict[str, Any]) -> str:
    """Deterministic case-2 reply when the formatter fails: picks + caveat + ask."""
    asked = category_ask.get("asked_text") or "that category"
    funds = [f["fund"] for f in category_ask.get("top_funds") or []]
    if funds:
        lead = (
            f"Our top-ranked {asked} picks are: " + ", ".join(funds) + ". "
            "That said, we'd recommend spreading fresh money across your "
            "goal-based plan rather than concentrating in one category."
        )
    else:
        lead = (
            f"We don't have ranked funds in {asked} right now — our "
            "recommendations follow your goal-based plan instead."
        )
    return (
        lead
        + " How much would you like to invest, and should it be a one-time "
        "lumpsum or a monthly SIP?"
    )


def _build_fallback_ainv_brief(output: AdditionalInvestmentOutput, category_ask: dict[str, Any] | None = None) -> str:
    """Render the engine output as a chat-ready markdown brief that NAMES the
    funds to buy. BUY-only — no sells, no tax math. Used when the formatter
    fails so the customer always sees where their money is going."""
    deploy_inr = float(output.deploy_amount_inr)
    deploy_indian = format_inr_indian(deploy_inr)
    is_sip = output.cadence == Cadence.SIP_MONTHLY
    note = _under_deploy_note(
        deploy_inr,
        float(output.undeployed_inr),
        len(output.per_subgroup_target),
        bool(output.buys),
    )

    out: list[str] = []

    # Nothing to buy (empty target bucket / no eligible funds) — no table.
    if not output.buys:
        out.append((note or f"I couldn't deploy your {deploy_indian} right now."))
    else:
        if is_sip:
            out.append(
                f"Here's how I'd put your **{deploy_indian}/month** SIP to work, "
                "split across these funds:"
            )
        else:
            out.append(
                f"Here's how I'd deploy your **{deploy_indian}** across these funds:"
            )
        out.append("")

        if is_sip:
            out.append("| Buy into | Monthly |")
            out.append("| --- | ---: |")
        else:
            out.append("| Buy into | Amount |")
            out.append("| --- | ---: |")

        for b in sorted(output.buys, key=lambda x: -float(x.amount_inr)):
            if is_sip:
                monthly_indian = (
                    format_inr_indian(float(b.monthly_amount_inr))
                    if b.monthly_amount_inr is not None
                    else "—"
                )
                out.append(f"| {b.recommended_fund} | {monthly_indian} |")
            else:
                out.append(
                    f"| {b.recommended_fund} | {format_inr_indian(float(b.amount_inr))} |"
                )
        out.append("")

        if note is not None:
            out.append(f"_{note}_")

    if category_ask is not None:
        out.append("")
        out.append(_fallback_category_line(category_ask))

    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Formatter wrapper
# ---------------------------------------------------------------------------


async def _format_or_fallback_ainv(
    ctx: TurnContext,
    output: AdditionalInvestmentOutput,
    deficit_facts: list[dict[str, Any]] | None = None,
    category_ask: dict[str, Any] | None = None,
) -> str:
    """Run the SHARED formatter on the engine output; fall back to the
    deterministic fund-naming brief on FormatterFailure. Deficit runs (lumpsum)
    get the gap-fill body prompt + deficit_rows facts. When the customer asked
    about a specific category, ``category_ask`` is threaded into both the facts
    pack and the deterministic fallback so neither path ever ignores it."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_ainv_facts_pack(
            output, deficit_rows=deficit_facts, category_ask=category_ask
        ),
        body_prompt=(
            _AINV_DEFICIT_FORMATTER_BODY
            if deficit_facts is not None
            else _AINV_FORMATTER_BODY
        ),
        module_name="additional_investment",
        action_mode="compute",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_ainv_brief(
            output, category_ask=category_ask
        ),
    )


def _build_category_ask(
    raw_category: str,
    category: str | None,
    deficit_facts: list[dict[str, Any]] | None,
    buys: list[Any],
) -> dict[str, Any]:
    """Assemble the contractual category_ask block (spec 2026-07-04)."""
    top = top_funds_for_category(category) if category else []
    status = category_status(
        category,
        deficit_facts=deficit_facts,
        buys=buys,
        exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
    )
    subgroup = category_subgroup(category) if category else None
    row = next(
        (r for r in (deficit_facts or []) if r.get("subgroup") == subgroup), None
    )
    return {
        "asked_text": raw_category,
        "category": category,
        "status": status,
        "top_funds": [
            {"fund": r.fund_name, "sub_category": r.sub_category} for r in top
        ],
        "subgroup_ideal_inr": row.get("ideal_inr") if row else None,
        "subgroup_current_inr": row.get("current_inr") if row else None,
    }


async def _format_category_probe(
    ctx: TurnContext, category_ask: dict[str, Any]
) -> str:
    """Case 2 (category, no amount): picks + caveat + ask, via the shared
    formatter; deterministic probe brief on failure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack={"category_ask": category_ask},
        body_prompt=_AINV_CATEGORY_PROBE_BODY,
        module_name="additional_investment",
        action_mode="category_probe",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_category_probe(category_ask),
    )


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


_MSG_ASK_AMOUNT = (
    "Happy to help you put fresh money to work — how much would you like to "
    "invest, and should it be a one-time lumpsum or a monthly SIP?"
)


@register("additional_investment")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Parse the deploy request, compute the BUY list, and format it.

    BUY-only / write-once: there is no follow-up classifier, so every turn on
    this intent recomputes the deployment and re-formats it in `compute` mode.
    First the deploy amount + cadence are parsed from the question; a missing
    amount short-circuits to a clarify reply (amount + lumpsum/SIP). When the
    orchestrator returns a ``blocking_message`` (failed pre-check / incomplete
    profile) the handler relays that gate text via ``format_relay_or_canned``
    rather than formatting a BUY list. On the success path the persisted run id
    (set by the orchestrator when persist=True) is surfaced on
    ``ChatHandlerResult.additional_investment_run_id`` for the HTTP layer; the
    persistence itself is owned by the orchestrator and the persist service.
    """
    amount, cadence, raw_category = await extract_deploy_request(
        ctx.user_question, ctx.conversation_history
    )
    category = resolve_category(raw_category) if raw_category else None

    if amount is None or amount <= 0:
        if raw_category is not None:
            # Case 2 (spec 2026-07-04): answer the category question honestly,
            # then ask for the amount — never a dead end, never a hallucinated
            # capability.
            category_ask = _build_category_ask(raw_category, category, None, [])
            text = await _format_category_probe(ctx, category_ask)
            return ChatHandlerResult(text=text)
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=_MSG_ASK_AMOUNT,
        )
        return ChatHandlerResult(text=text)

    outcome = await compute_additional_investment_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        deploy_amount_inr=amount,
        cadence=cadence,
        chat_ctx=ctx,
        persist=True,  # Plan 3b: persist the recommendation row
        focus_category=category,
    )
    if outcome.blocking_message:
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=outcome.blocking_message,
        )
        return ChatHandlerResult(text=text)

    category_ask = (
        _build_category_ask(
            raw_category, category, outcome.deficit_facts, outcome.output.buys
        )
        if raw_category is not None
        else None
    )
    text = await _format_or_fallback_ainv(
        ctx,
        outcome.output,
        deficit_facts=outcome.deficit_facts,
        category_ask=category_ask,
    )
    return ChatHandlerResult(
        text=text,
        additional_investment_run_id=outcome.run_id,
    )
