"""Bridge: portfolio_query intent — wraps the AI_Agents.portfolio_query orchestrator.

Maps the User ORM (with eager-loaded holdings → fund_metadata) into the agent's
ClientContext / PortfolioContext DTOs, calls PortfolioQueryOrchestrator, and
formats the response for the chat layer.

Market commentary is read by the orchestrator from
`AI_Agents/Reference_docs/market_commentary_latest.md` (written by the
market_commentary agent / `app/services/ai_bridge/market_commentary_service.py`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from app.config import get_settings
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from portfolio_query import (
    AllocationRow,
    ClientContext,
    ConversationTurn,
    Holding,
    LLMClient,
    PortfolioContext,
    PortfolioQueryOrchestrator,
    SubCategoryAllocationRow,
)


logger = logging.getLogger(__name__)


_NO_PORTFOLIO_TEMPLATE = (
    "Hi {first_name}, I couldn't find an active portfolio on your account yet. "
    "Once you add holdings or allocation details, I can answer questions like "
    "your biggest holding, allocation breakdown, and overall performance."
)
_MISSING_KEY_REPLY = (
    "I can't reach the language model right now — the Anthropic API key isn't "
    "configured on the server. Please set `ANTHROPIC_API_KEY` and try again."
)
_MISSING_COMMENTARY_REPLY = (
    "I can't answer that yet — the market commentary file isn't available. "
    "Please ask a market question first to refresh it, then try again."
)
_GENERIC_FAILURE_REPLY = (
    "I couldn't generate a portfolio answer right now. Please try rephrasing your question."
)


# ---------------------------------------------------------------------------
# Lazy orchestrator singleton (skill .md parsed once per process)
# ---------------------------------------------------------------------------

_orchestrator: PortfolioQueryOrchestrator | None = None


def _get_orchestrator(api_key: str) -> PortfolioQueryOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PortfolioQueryOrchestrator(LLMClient(api_key))
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
    investment_horizon = getattr(rp, "investment_horizon", None) if rp is not None else None
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


def _build_holdings(orm_holdings: Iterable[Any]) -> list[Holding]:
    out: list[Holding] = []
    for h in orm_holdings:
        name = (
            getattr(h, "instrument_name", None)
            or getattr(h, "ticker_symbol", None)
            or "Unknown"
        )
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
            )
        )
    return out


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


def _build_portfolio_context(user: Any) -> PortfolioContext | None:
    portfolios = list(getattr(user, "portfolios", []) or [])
    if not portfolios:
        return None
    primary = next(
        (p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0]
    )

    orm_holdings = list(getattr(primary, "holdings", []) or [])
    orm_allocations = list(getattr(primary, "allocations", []) or [])

    total_value = float(getattr(primary, "total_value", 0) or 0)

    return PortfolioContext(
        total_value_inr=_f(primary, "total_value"),
        total_invested_inr=_f(primary, "total_invested"),
        total_gain_percentage=_f(primary, "total_gain_percentage"),
        holdings=_build_holdings(orm_holdings),
        allocations=_build_allocation_rows(orm_allocations),
        sub_category_allocations=_build_subcategory_rows(orm_holdings, total_value),
    )


# ---------------------------------------------------------------------------
# Conversation history mapping
# ---------------------------------------------------------------------------


def _build_history(history: list[dict[str, str]] | None) -> list[ConversationTurn]:
    if not history:
        return []
    turns: list[ConversationTurn] = []
    for msg in history[-6:]:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role not in ("user", "assistant"):
            continue
        turns.append(ConversationTurn(role=role, content=content))
    return turns


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_portfolio_query_response(
    user: Any,
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Answer the user's portfolio question via the AI_Agents.portfolio_query agent."""

    portfolio = _build_portfolio_context(user)
    if portfolio is None:
        first_name = getattr(user, "first_name", None) or "there"
        return _NO_PORTFOLIO_TEMPLATE.format(first_name=first_name)

    api_key = get_settings().get_anthropic_key()
    if not api_key:
        return _MISSING_KEY_REPLY

    client_ctx = _build_client_context(user)
    history = _build_history(conversation_history)

    try:
        result = await _get_orchestrator(api_key).run(
            question=user_question,
            client=client_ctx,
            portfolio=portfolio,
            conversation_history=history,
        )
    except FileNotFoundError as exc:
        logger.warning("portfolio_query: market commentary file missing — %s", exc)
        return _MISSING_COMMENTARY_REPLY
    except Exception:
        logger.exception("portfolio_query: orchestrator failed")
        return _GENERIC_FAILURE_REPLY

    return result.answer or result.redirect_message or _GENERIC_FAILURE_REPLY
