"""Portfolio-query domain service — wraps the AI_Agents.portfolio_query orchestrator.

This is the portfolio domain's gateway to the portfolio_query agent. Maps the
User ORM (with eager-loaded holdings → fund_metadata) into the agent's
ClientContext / PortfolioContext DTOs, calls PortfolioQueryOrchestrator, and
formats the response for the chat layer.

Public entry: ``answer_portfolio_query(question, ctx)`` — called by the brain's
``flow_portfolio_query``. The brain never imports the agent directly.

Market commentary is read by the orchestrator from
`AI_Agents/Reference_docs/market_commentary_latest.md` (written by the
market_commentary domain service).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from app.core.config import get_settings
from app.domains.ai_engine.answer_formatter import format_with_telemetry
from app.domains.ai_engine.common import ensure_ai_agents_path, with_gap_notes
from app.domains.ai_engine.types import AIModule
from app.domains.chat.services.ai_module_telemetry import record_ai_module_run

ensure_ai_agents_path()

from portfolio_query import (
    AllocationRow,
    ClientContext,
    Holding,
    PortfolioContext,
    PortfolioQueryOrchestrator,
    SubCategoryAllocationRow,
)
from portfolio_query.models import _DEFAULT_REDIRECT  # noqa: E402  shared canned redirect


logger = logging.getLogger(__name__)


_NO_PORTFOLIO_TEMPLATE = (
    "Hi {first_name}, I couldn't find an active portfolio on your account yet. "
    "Once you add holdings or allocation details, I can answer questions like "
    "your biggest holding, allocation breakdown, and overall performance."
)
_MISSING_KEY_REPLY = (
    "I can't reach the language model right now — the Anthropic API key isn't "
    "configured on the server. Please set `PORTFOLIO_QUERY_API_KEY` or `ANTHROPIC_API_KEY` in `.env`."
)
_MISSING_COMMENTARY_REPLY = (
    "I'll have a sharper answer once the latest market commentary is loaded "
    "— ask me a quick market question first (e.g. 'how are markets doing "
    "today?') to refresh it, and then I'll be ready to dig into this for you."
)
_GENERIC_FAILURE_REPLY = (
    "Hmm, that one didn't come through cleanly on my end — give the question "
    "another phrasing and I'll take another shot at it."
)


# ---------------------------------------------------------------------------
# Lazy orchestrator singleton (skill .md parsed once per process)
# ---------------------------------------------------------------------------

_orchestrator: PortfolioQueryOrchestrator | None = None


def _get_orchestrator() -> PortfolioQueryOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PortfolioQueryOrchestrator()
    return _orchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIN_AGE = 18


def _age_from_dob(dob: date) -> int:
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(_MIN_AGE, age)


def _f(obj: Any, attr: str) -> float | None:
    val = getattr(obj, attr, None)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _holding_invested_and_gain(
    h: Any,
) -> tuple[float | None, float | None, float | None]:
    """Derive (invested_inr, gain_inr, gain_pct) from cost basis when possible.

    Returns ``(None, None, None)`` if avg_cost × quantity isn't computable. Uses
    `current_value` directly when present; otherwise falls back to
    `quantity × current_price`.
    """
    qty = _f(h, "quantity")
    avg = _f(h, "average_cost")
    if qty is None or avg is None:
        return None, None, None
    invested = qty * avg
    if invested <= 0:
        return None, None, None
    cv = _f(h, "current_value")
    if cv is None:
        cur_px = _f(h, "current_price")
        cv = qty * cur_px if cur_px is not None else None
    if cv is None:
        return round(invested, 2), None, None
    gain = cv - invested
    gain_pct = (gain / invested) * 100.0
    return round(invested, 2), round(gain, 2), round(gain_pct, 2)


def _xirr(flows: Sequence[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Newton-Raphson XIRR. Returns annualised rate as a percentage, or None.

    `flows` must contain ≥1 negative (outflow / buy) and ≥1 positive (inflow /
    current value at today) entries with strictly-defined dates. Returns None
    when the inputs are degenerate or the solver doesn't converge.
    """
    if len(flows) < 2:
        return None
    has_pos = any(amt > 0 for _, amt in flows)
    has_neg = any(amt < 0 for _, amt in flows)
    if not (has_pos and has_neg):
        return None
    sorted_flows = sorted(flows, key=lambda x: x[0])
    t0 = sorted_flows[0][0]

    def years(d: date) -> float:
        return (d - t0).days / 365.0

    rate = guess
    for _ in range(60):
        try:
            npv = 0.0
            d_npv = 0.0
            for d, amt in sorted_flows:
                t = years(d)
                discount = (1.0 + rate) ** t
                if discount == 0:
                    return None
                npv += amt / discount
                d_npv += -t * amt / (discount * (1.0 + rate))
            if abs(npv) < 1e-6:
                return round(rate * 100.0, 2)
            if d_npv == 0:
                return None
            rate -= npv / d_npv
            if rate <= -0.999:
                rate = -0.999
        except (ZeroDivisionError, OverflowError, ValueError):
            return None
    return None


def _compute_portfolio_xirr(user: Any, total_value: float | None) -> float | None:
    """Compute XIRR over the user's MF cash flows + the current portfolio value as a synthetic inflow.

    Direction is driven off ``transaction_type`` (not the amount's sign) using
    the real ``MfTransactionType`` enum that CAMS/AA ingest writes: money put in
    (BUY, SWITCH_IN) is an outflow; money taken out (SELL, SWITCH_OUT) is an
    inflow. ``abs(amount)`` is used for magnitude because ingest may store sells
    as negative. DIVIDEND_REINVEST is excluded — no external cash moves, so per
    standard XIRR it must not count as a contribution. Previously this matched
    non-existent "REDEEM"/"REDEMPTION" types and skipped switches, distorting XIRR.
    """
    if not total_value or total_value <= 0:
        return None
    txns = list(getattr(user, "mf_transactions", None) or [])
    if not txns:
        return None
    # Outflow types are money leaving the wallet into funds (negative); inflow
    # types are money coming back out (positive). Anything else is skipped.
    OUTFLOW_TYPES = {"BUY", "SWITCH_IN"}
    INFLOW_TYPES = {"SELL", "SWITCH_OUT"}
    flows: list[tuple[date, float]] = []
    for t in txns:
        amt = _f(t, "amount")
        d = getattr(t, "transaction_date", None)
        if amt is None or amt == 0 or d is None:
            continue
        amt = abs(amt)
        # Coerce datetime → date.
        if isinstance(d, datetime):
            d = d.date()
        # transaction_type may be an enum (`MfTransactionType.BUY`) whose
        # ``str()`` returns "ClassName.MEMBER" rather than the value. Pull
        # `.value` when it exists; otherwise stringify and uppercase.
        raw_type = getattr(t, "transaction_type", None)
        ttype = (getattr(raw_type, "value", None) or str(raw_type or "")).upper()
        if ttype in OUTFLOW_TYPES:
            flows.append((d, -amt))
        elif ttype in INFLOW_TYPES:
            flows.append((d, amt))
        else:
            # DIVIDEND_REINVEST / unknown: skip rather than guess sign.
            continue
    if not flows:
        return None
    flows.append((date.today(), float(total_value)))
    return _xirr(flows)


# ---------------------------------------------------------------------------
# ORM → ClientContext
# ---------------------------------------------------------------------------


def _build_client_context(user: Any) -> ClientContext:
    rp = getattr(user, "risk_profile", None)
    era = getattr(user, "effective_risk_assessment", None)
    inv = getattr(user, "investment_profile", None)

    dob = getattr(user, "date_of_birth", None)
    age = _age_from_dob(dob) if dob is not None else None

    risk_category = rp.risk_category if rp is not None else None
    investment_horizon = (
        getattr(rp, "investment_horizon", None) if rp is not None else None
    )
    occupation_type = getattr(rp, "occupation_type", None) if rp is not None else None

    effective_risk_score = _f(era, "effective_risk_score") if era is not None else None
    annual_income = _f(inv, "annual_income") if inv is not None else None
    total_liabilities = _f(inv, "total_liabilities") if inv is not None else None

    goals: list[str] = []
    for g in getattr(user, "financial_goals", []) or []:
        name = getattr(g, "goal_name", None)
        if name:
            goals.append(str(name))

    return ClientContext(
        age=age,
        risk_category=risk_category,
        effective_risk_score=effective_risk_score,
        investment_horizon=investment_horizon,
        occupation_type=occupation_type,
        annual_income_inr=annual_income,
        total_liabilities_inr=total_liabilities,
        financial_goals=goals,
    )


# ---------------------------------------------------------------------------
# ORM → PortfolioContext
# ---------------------------------------------------------------------------


def _holding_asset_class(holding: Any) -> str | None:
    md = getattr(holding, "fund_metadata", None)
    if md is not None:
        cat = getattr(md, "category", None)
        if cat:
            return str(cat)
    # Fallback for non-MF holdings (stocks, ETFs, etc.) — coarse but honest.
    itype = getattr(holding, "instrument_type", None)
    return str(itype) if itype else None


def _holding_sub_category(holding: Any) -> str | None:
    md = getattr(holding, "fund_metadata", None)
    if md is None:
        return None
    sub = getattr(md, "sub_category", None)
    return str(sub) if sub else None


def _build_holdings(
    orm_holdings: Iterable[Any],
    xirr_by_scheme: dict[str, float] | None = None,
) -> list[Holding]:
    xirr_by_scheme = xirr_by_scheme or {}
    out: list[Holding] = []
    for h in orm_holdings:
        name = (
            getattr(h, "instrument_name", None)
            or getattr(h, "ticker_symbol", None)
            or "Unknown"
        )
        invested, gain, gain_pct = _holding_invested_and_gain(h)
        out.append(
            Holding(
                name=str(name),
                instrument_type=getattr(h, "instrument_type", None),
                asset_class=_holding_asset_class(h),
                sub_category=_holding_sub_category(h),
                quantity=_f(h, "quantity"),
                current_value_inr=_f(h, "current_value"),
                allocation_percentage=_f(h, "allocation_percentage"),
                return_1y_pct=_f(h, "return_1y"),
                return_3y_pct=_f(h, "return_3y"),
                invested_amount_inr=invested,
                gain_inr=gain,
                gain_pct=gain_pct,
                # ticker_symbol IS the scheme_code — same key the fund_metadata
                # relationship joins on (portfolio.py:136).
                xirr_pct=xirr_by_scheme.get(str(getattr(h, "ticker_symbol", "") or "")),
            )
        )
    return out


async def _xirr_by_scheme(db: Any, user_id: Any) -> dict[str, float]:
    """Per-fund XIRR from the latest-snapshot table, keyed by scheme_code.

    Returns {} on any failure — a missing XIRR degrades one line of an answer,
    it must never fail the turn.
    """
    if db is None or user_id is None:
        return {}
    try:
        from sqlalchemy import select

        from app.domains.mutual_funds.models import UserMfLatestSnapshot

        rows = (
            await db.execute(
                select(
                    UserMfLatestSnapshot.scheme_code, UserMfLatestSnapshot.xirr_pct
                ).where(UserMfLatestSnapshot.user_id == user_id)
            )
        ).all()
        return {str(code): float(x) for code, x in rows if x is not None}
    except Exception:
        logger.warning("per-fund XIRR lookup failed; answering without it", exc_info=True)
        return {}


def _build_allocation_rows(orm_allocations: Iterable[Any]) -> list[AllocationRow]:
    out: list[AllocationRow] = []
    for a in orm_allocations:
        ac = getattr(a, "asset_class", None)
        if not ac:
            continue
        out.append(
            AllocationRow(
                asset_class=str(ac),
                percentage=float(getattr(a, "allocation_percentage", 0) or 0),
                amount_inr=_f(a, "amount"),
            )
        )
    return out


def _build_subcategory_rows(
    orm_holdings: Iterable[Any], total_value: float
) -> list[SubCategoryAllocationRow]:
    """Bucket holdings by (asset_class, sub_category) using fund_metadata; sum amounts."""
    if total_value <= 0:
        return []
    buckets: dict[tuple[str | None, str], float] = defaultdict(float)
    for h in orm_holdings:
        sub = _holding_sub_category(h)
        if not sub:
            continue
        ac = _holding_asset_class(h)
        cv = float(getattr(h, "current_value", 0) or 0)
        if cv <= 0:
            continue
        buckets[(ac, sub)] += cv

    rows: list[SubCategoryAllocationRow] = []
    for (ac, sub), amount in sorted(buckets.items(), key=lambda kv: -kv[1]):
        rows.append(
            SubCategoryAllocationRow(
                asset_class=ac,
                sub_category=sub,
                percentage=round(100.0 * amount / total_value, 2),
                amount_inr=round(amount, 2),
            )
        )
    return rows


# Itemize at most this many holdings (largest by value) in the LLM prompt.
# Counts and the omitted-tail rollup below keep count / "do I hold X?" questions
# answerable; the skill prompt documents the contract. Most retail portfolios
# are well under this, so the cap only fires on outliers.
_MAX_ITEMIZED_HOLDINGS = 30


def _itemize_holdings(
    all_holdings: list[Holding],
) -> tuple[list[Holding], int | None, float | None]:
    """Largest-by-value slice + (omitted_count, omitted_value) for the tail."""
    if len(all_holdings) <= _MAX_ITEMIZED_HOLDINGS:
        return all_holdings, None, None
    ranked = sorted(
        all_holdings, key=lambda h: h.current_value_inr or 0.0, reverse=True
    )
    itemized = ranked[:_MAX_ITEMIZED_HOLDINGS]
    tail = ranked[_MAX_ITEMIZED_HOLDINGS:]
    omitted_value = round(sum(h.current_value_inr or 0.0 for h in tail), 2)
    return itemized, len(tail), omitted_value


def _count_by_type(all_holdings: list[Holding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for h in all_holdings:
        counts[h.instrument_type or "unknown"] += 1
    return dict(counts)


def _build_portfolio_context(
    user: Any, xirr_by_scheme: dict[str, float] | None = None
) -> PortfolioContext | None:
    portfolios = list(getattr(user, "portfolios", []) or [])
    if not portfolios:
        return None
    primary = next(
        (p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0]
    )

    orm_holdings = list(getattr(primary, "holdings", []) or [])
    orm_allocations = list(getattr(primary, "allocations", []) or [])

    total_value = float(getattr(primary, "total_value", 0) or 0)
    xirr_pct = _compute_portfolio_xirr(user, total_value)

    all_holdings = _build_holdings(orm_holdings, xirr_by_scheme)
    itemized, omitted_count, omitted_value = _itemize_holdings(all_holdings)

    return PortfolioContext(
        total_value_inr=_f(primary, "total_value"),
        total_invested_inr=_f(primary, "total_invested"),
        total_gain_percentage=_f(primary, "total_gain_percentage"),
        xirr_pct=xirr_pct,
        holdings=itemized,
        allocations=_build_allocation_rows(orm_allocations),
        # Sub-category rollups aggregate the FULL holdings list, not the
        # itemized slice — category questions stay exact under the cap.
        sub_category_allocations=_build_subcategory_rows(orm_holdings, total_value),
        total_holdings_count=len(all_holdings),
        holdings_count_by_type=_count_by_type(all_holdings),
        omitted_holdings_count=omitted_count,
        omitted_holdings_value_inr=omitted_value,
    )


# ---------------------------------------------------------------------------
# Conversation history mapping
# ---------------------------------------------------------------------------


# Prior REPLIES enter as excerpts. A goal-planning answer runs to ~14,000 chars
# (30-year cashflow table); six of those at the shared 24,000-char cap drown the
# prompt, and measured live the agent then answered the previous turn's thread
# instead of the question asked. History is here to resolve pronouns and
# shorthand — an excerpt does that, the full table only adds noise. The
# customer's own messages are never truncated: they are short, and they are the
# questions.
_MAX_HISTORY_REPLY_CHARS = 600
_HISTORY_EXCERPT_MARKER = " …"


def _build_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Shape chat history for the formatter's RECENT_HISTORY block, oldest first.

    The turn carries no timestamp, so the age of a resumed thread rides inside the
    content — this agent misread a fortnight-old goal question as live context on
    2026-07-25. Annotate BEFORE slicing so a gap that falls on the window edge
    still shows.
    """
    if not history:
        return []
    turns: list[dict[str, str]] = []
    for msg in with_gap_notes(history)[-6:]:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and len(content) > _MAX_HISTORY_REPLY_CHARS:
            content = content[:_MAX_HISTORY_REPLY_CHARS] + _HISTORY_EXCERPT_MARKER
        turns.append({"role": role, "content": content})
    return turns


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioQueryOutcome:
    """The reply, plus the agent's (advisory) view of where the question belongs.

    ``suggested_intent`` is REPORTED, NEVER ACTED ON — it exists so we can
    measure router/agent disagreement before deciding whether a routing handoff
    is worth building. Local failure paths below return text only.
    """

    text: str
    suggested_intent: str | None = None
    # Which internal path the agent took: "X" out of scope, "M" market, "P"
    # portfolio. Telemetry only — this agent has no detector and nothing branches
    # on it; the choice has already shaped the reply by the time we see it.
    path: str | None = None


# Non-prose control fields the portfolio skill sets alongside `answer`. Booleans,
# an enum and short strings — none of them compete with `answer` for prose.
_PORTFOLIO_TOOL_FIELDS: dict[str, Any] = {
    "guardrail_triggered": {
        "type": "boolean",
        "description": (
            "True if the question is out-of-scope per the guardrail rules. When true, "
            "set `answer` to null and provide `redirect_message`."
        ),
    },
    "redirect_message": {
        "type": ["string", "null"],
        "description": (
            "Polite, one-sentence redirect when `guardrail_triggered` is true. Null when "
            "the answer is in-scope."
        ),
    },
    "path": {
        "type": ["string", "null"],
        "enum": ["X", "M", "P", None],
        "description": (
            "Which path you took in Step 1: 'X' out of scope, 'M' general market, "
            "'P' portfolio-specific. Recorded for review; it does not change your reply. "
            "Always set it."
        ),
    },
    "suggested_intent": {
        "type": ["string", "null"],
        "description": (
            "Usually null. Set it only per the checklist's Step 4. Valid values: "
            "goal_planning, asset_allocation, rebalancing, additional_investment, "
            "general_market_query. Recorded for review; it does not change your reply."
        ),
    },
}


def _apply_guardrail_backstop(text: str, extras: dict[str, Any]) -> str:
    """Deterministic backstop: an out-of-scope `answer` must never reach the customer.

    Mirrors ``PortfolioQueryResponse._enforce_guardrail_contract``. When the guardrail
    fires we discard ``answer`` outright — the model may have written the out-of-scope
    reply before deciding — and send the redirect instead.
    """
    if not extras.get("guardrail_triggered"):
        return text or _GENERIC_FAILURE_REPLY
    redirect = extras.get("redirect_message")
    if isinstance(redirect, str) and redirect.strip():
        return redirect.strip()
    return _DEFAULT_REDIRECT


async def generate_portfolio_query_response(
    user: Any,
    user_question: str,
    conversation_history: list[dict[str, Any]] | None = None,
    db: Any = None,
    user_id: Any = None,
    want_market_commentary: bool = True,
    ctx: Any = None,
) -> PortfolioQueryOutcome:
    """Answer the user's portfolio question via the AI_Agents.portfolio_query agent.

    The agent supplies the facts pack and the skill body; the shared answer
    formatter makes the LLM call and writes the reply.
    """

    portfolio = _build_portfolio_context(user, await _xirr_by_scheme(db, user_id))
    if portfolio is None:
        first_name = getattr(user, "first_name", None) or "there"
        return PortfolioQueryOutcome(
            _NO_PORTFOLIO_TEMPLATE.format(first_name=first_name)
        )

    api_key = (
        get_settings().get_anthropic_portfolio_query_key()
        or get_settings().get_anthropic_key()
    )
    if not api_key:
        return PortfolioQueryOutcome(_MISSING_KEY_REPLY)

    client_ctx = _build_client_context(user)

    try:
        orch = _get_orchestrator()
        facts_pack = orch.build_facts(
            client=client_ctx,
            portfolio=portfolio,
            want_market_commentary=want_market_commentary,
        )
    except FileNotFoundError as exc:
        logger.warning("portfolio_query: market commentary file missing — %s", exc)
        return PortfolioQueryOutcome(_MISSING_COMMENTARY_REPLY)
    except Exception:
        logger.exception("portfolio_query: facts build failed")
        return PortfolioQueryOutcome(_GENERIC_FAILURE_REPLY)

    extras: dict[str, Any] = {}
    text = await format_with_telemetry(
        ctx=ctx,
        facts_pack=facts_pack,
        body_prompt=orch.query_body,
        module_name="portfolio_query",
        action_mode="narrate",
        profile={"first_name": getattr(user, "first_name", None)},
        build_fallback=lambda: _GENERIC_FAILURE_REPLY,
        history_override=_build_history(conversation_history),
        extra_tool_fields=_PORTFOLIO_TOOL_FIELDS,
        extras_out=extras,
        # Path X nulls `answer` and answers via `redirect_message` — an empty
        # answer is the guardrail firing, not a formatter failure.
        allow_empty_answer=True,
    )

    return PortfolioQueryOutcome(
        text=_apply_guardrail_backstop(text, extras),
        suggested_intent=extras.get("suggested_intent"),
        path=extras.get("path"),
    )


# ---------------------------------------------------------------------------
# Public domain entry — the brain's portfolio_query flow calls this.
# ---------------------------------------------------------------------------


async def answer_portfolio_query(question: str, ctx) -> str:
    """Answer a question about the user's current portfolio (read-only).

    ``ctx`` is the brain's ``TurnContext`` — it already carries the preloaded
    user ORM graph and the conversation history this function needs, so the
    flow can call us with just ``(question, ctx)``.
    """
    outcome = await generate_portfolio_query_response(
        user=ctx.user_ctx,
        user_question=question,
        conversation_history=ctx.conversation_history,
        db=getattr(ctx, "db", None),
        user_id=ctx.effective_user_id,
        want_market_commentary="market_commentary"
        in (getattr(ctx, "tools_needed", ()) or ()),
        ctx=ctx,
    )
    await _record_path(ctx, outcome)
    await _record_intent_disagreement(question, ctx, outcome)
    return outcome.text


async def _record_path(ctx, outcome: PortfolioQueryOutcome) -> None:
    """Note which of Path X / M / P the agent chose.

    Kept as its own row rather than folded into the disagreement row: the path is
    recorded every answered turn, a disagreement is rare, and mixing them would
    make either one awkward to query. Best-effort — telemetry never costs a turn.
    """
    if not outcome.path:
        return
    try:
        await record_ai_module_run(
            getattr(ctx, "db", None),
            user_id=ctx.effective_user_id,
            session_id=getattr(ctx, "session_id", None),
            module=AIModule.PORTFOLIO_QUERY.value,
            reason="path",
            intent_detected=AIModule.PORTFOLIO_QUERY.value,
            extra={"path": outcome.path},
        )
    except Exception:
        logger.exception("portfolio_query: failed to record path")


async def _record_intent_disagreement(
    question: str,
    ctx,
    outcome: PortfolioQueryOutcome,
) -> None:
    """Note that the agent would have sent this question elsewhere.

    Reaching this module means the router chose ``portfolio_query``, so a
    ``suggested_intent`` naming anything else IS the disagreement. Recorded, not
    acted on — the reply above is already the customer's. Best-effort: telemetry
    must never cost a turn.
    """
    suggested = outcome.suggested_intent
    if not suggested or suggested == AIModule.PORTFOLIO_QUERY.value:
        return
    try:
        await record_ai_module_run(
            getattr(ctx, "db", None),
            user_id=ctx.effective_user_id,
            session_id=getattr(ctx, "session_id", None),
            module=AIModule.PORTFOLIO_QUERY.value,
            reason="intent_disagreement",
            intent_detected=AIModule.PORTFOLIO_QUERY.value,
            extra={
                "router_intent": AIModule.PORTFOLIO_QUERY.value,
                "agent_suggested_intent": suggested,
                "question": question[:500],
            },
        )
    except Exception:
        logger.exception("portfolio_query: failed to record intent disagreement")
