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
from typing import Any

from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.ai_engine.answer_formatter import (
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.common import ensure_ai_agents_path, format_inr_indian
from app.domains.additional_investment.services.ainv_engine.service import (
    compute_additional_investment_result,
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
    r"(crores?|cr|lakhs?|lacs?|l|thousand|k)?",
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
# Formatter body prompt (mirrors _REBAL_FORMATTER_BODY: documents the
# FACTS_PACK shape + per-ActionMode lead/length budget; no narrative prose)
# ---------------------------------------------------------------------------

_AINV_FORMATTER_BODY = """You are answering a customer's question about a fresh
additional-investment recommendation — NEW money being deployed (a one-time
lumpsum or a monthly SIP) into specific funds. This is BUY-only: nothing is ever
sold. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  deploy_amount_inr / deploy_amount_indian — total fresh money being deployed.
  cadence: "lumpsum" or "sip_monthly". lumpsum = a single one-time deployment;
           sip_monthly = the same plan repeats every month. When cadence is
           sip_monthly, frame amounts per-month (use each buy's
           monthly_amount_indian), not the one-time amount.
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
  under_deploy_note — present ONLY when undeployed_inr > 0: a one-line plain-
           English nudge explaining the leftover and that the emergency reserve
           is always excluded from fresh deployment. Surface it when present.

  per_subgroup_target: list, one entry per subgroup the deploy was split into:
      subgroup    — internal engine grouping (e.g. "low_beta_equities"). DO NOT
                    surface this raw label; it is context only.
      ratio       — this subgroup's renormalised share of the deploy (0-1).
      target_inr  — the rupee amount targeted at this subgroup.

  buys: list of the specific funds to BUY — this is the substance of the answer:
      recommended_fund — the customer-facing scheme name (e.g. "HDFC Top 100").
                    Cite this VERBATIM; naming the funds is the point of the reply.
      sub_category  — SEBI category for context (e.g. "Large Cap Fund").
      amount_inr / amount_indian — the one-time amount to put into this fund.
      monthly_amount_inr / monthly_amount_indian — the per-month amount when
                    cadence is sip_monthly (null for lumpsum).

ACTION_MODE tells you the situation. ACTION_MODE is `compute` here — it is set
by the system on a fresh first-turn recommendation (it is not produced by a
classifier). Per-mode behavior:

  compute    — first-time additional-investment recommendation; introduce it
               shaped by the customer's question. Lead with the headline
               (deploy_amount_indian with the cadence framing — one-time for
               lumpsum, per-month for sip_monthly), then NAME the 1-3 biggest
               buys with their amounts (monthly_amount_indian when sip_monthly,
               else amount_indian), and give one plain-English line on why the
               split leans the way it does (derived from target_bucket). Always
               name at least the largest fund(s) — the customer asked where
               their money is going. If undeployed_inr > 0, close with the
               under_deploy_note. Length: 6-10 sentences (fewer when there is a
               single buy).
"""


# ---------------------------------------------------------------------------
# Facts pack
# ---------------------------------------------------------------------------


def build_ainv_facts_pack(output: AdditionalInvestmentOutput) -> dict[str, Any]:
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
        amount_inr = float(b.amount_inr)
        monthly_inr = (
            float(b.monthly_amount_inr) if b.monthly_amount_inr is not None else None
        )
        buys.append(
            {
                "recommended_fund": b.recommended_fund,
                "sub_category": b.sub_category,
                "amount_inr": amount_inr,
                "amount_indian": format_inr_indian(amount_inr),
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
    if undeployed_inr > 0:
        facts["under_deploy_note"] = (
            f"{format_inr_indian(undeployed_inr)} of your "
            f"{format_inr_indian(deploy_inr)} couldn't be placed — per-fund caps "
            "or a shortage of eligible funds left a remainder, and your emergency "
            "reserve is always kept out of fresh deployment."
        )
    return facts


# ---------------------------------------------------------------------------
# Deterministic fallback (used when the formatter LLM call fails)
# ---------------------------------------------------------------------------


def _build_fallback_ainv_brief(output: AdditionalInvestmentOutput) -> str:
    """Render the engine output as a chat-ready markdown brief that NAMES the
    funds to buy. BUY-only — no sells, no tax math. Used when the formatter
    fails so the customer always sees where their money is going."""
    deploy_indian = format_inr_indian(float(output.deploy_amount_inr))
    is_sip = output.cadence == Cadence.SIP_MONTHLY

    out: list[str] = []
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
        out.append("| Buy into | Monthly | One-time |")
        out.append("| --- | ---: | ---: |")
    else:
        out.append("| Buy into | Amount |")
        out.append("| --- | ---: |")

    for b in sorted(output.buys, key=lambda x: -float(x.amount_inr)):
        amount_indian = format_inr_indian(float(b.amount_inr))
        if is_sip:
            monthly_indian = (
                format_inr_indian(float(b.monthly_amount_inr))
                if b.monthly_amount_inr is not None
                else "—"
            )
            out.append(
                f"| {b.recommended_fund} | {monthly_indian} | {amount_indian} |"
            )
        else:
            out.append(f"| {b.recommended_fund} | {amount_indian} |")
    out.append("")

    if float(output.undeployed_inr) > 0:
        out.append(
            f"_{format_inr_indian(float(output.undeployed_inr))} couldn't be placed "
            "under the per-fund caps — small enough to top up later. Your emergency "
            "reserve is kept out of fresh deployment._"
        )
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Formatter wrapper
# ---------------------------------------------------------------------------


async def _format_or_fallback_ainv(
    ctx: TurnContext,
    output: AdditionalInvestmentOutput,
) -> str:
    """Run the SHARED formatter on the engine output; fall back to the
    deterministic fund-naming brief on FormatterFailure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_ainv_facts_pack(output),
        body_prompt=_AINV_FORMATTER_BODY,
        module_name="additional_investment",
        action_mode="compute",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_ainv_brief(output),
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
    rather than formatting a BUY list. ChatHandlerResult has no ainv-specific id
    field, so only ``text`` is set; persistence/telemetry of the run is handled
    inside the orchestrator (Task 3a-T4) and the persist service (Task 3b).
    """
    amount, cadence = parse_deploy_request(ctx.user_question)
    if amount is None:
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
    )
    if outcome.blocking_message:
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=outcome.blocking_message,
        )
        return ChatHandlerResult(text=text)

    text = await _format_or_fallback_ainv(ctx, outcome.output)
    return ChatHandlerResult(text=text)
