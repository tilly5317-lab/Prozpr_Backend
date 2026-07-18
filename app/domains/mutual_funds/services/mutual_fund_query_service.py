"""mutual_fund_query app-service — builds the grounded facts pack (and, in Task 8, the
chat gateway).

The bundled ``mutual_fund_query`` engine is DB-agnostic; this is the one place that reads
the DB + ranking CSV to fill a ``MutualFundQueryFacts`` for the engine to narrate.
"""

from __future__ import annotations

import logging

from anthropic import AuthenticationError as AnthropicAuthenticationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.ai_engine.answer_formatter import format_with_telemetry
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.category_resolver import resolve_category
from app.domains.mutual_funds.services.fund_ranking_lookup import (
    peers_by_sub_category,
    ranking_by_isin,
)
from app.domains.mutual_funds.services.fund_resolver_service import (
    Ambiguous,
    ResolvedFund,
    resolve_fund,
)
from app.domains.mutual_funds.services.fund_returns_service import trailing_cagr_for_scheme
from app.domains.mutual_funds.services.fund_screener_service import (
    ScreenedFund,
    screen_top_funds,
)

ensure_ai_agents_path()
from mutual_fund_query import (  # noqa: E402
    ConversationTurn,
    FundFacts,
    MutualFundQueryFacts,
    MutualFundQueryResponse,
    FundReturns,
    PeerFund,
)
from mutual_fund_query.llm_client import LLMClient  # noqa: E402
from mutual_fund_query.orchestrator import MutualFundQueryOrchestrator  # noqa: E402

logger = logging.getLogger(__name__)

_GENERIC_FAILURE_REPLY = (
    "Sorry — I ran into a problem pulling that fund's details just now. "
    "Please try again in a moment."
)

_orchestrator: MutualFundQueryOrchestrator | None = None


def _get_orchestrator() -> MutualFundQueryOrchestrator:
    """Lazy process-singleton; resolves the Anthropic key on first use."""
    global _orchestrator
    if _orchestrator is None:
        api_key = get_settings().get_anthropic_key()
        if not api_key:
            raise RuntimeError("No Anthropic API key configured for mutual_fund_query")
        _orchestrator = MutualFundQueryOrchestrator(LLMClient(api_key))
    return _orchestrator


async def _fund_facts_for(db: AsyncSession, resolved: ResolvedFund) -> FundFacts:
    returns = FundReturns(**await trailing_cagr_for_scheme(db, resolved.scheme_code))
    row = ranking_by_isin(resolved.isin) if resolved.isin else None
    if row is not None:
        return FundFacts(
            fund_name=row.fund_name,
            sub_category=row.sub_category,
            returns=returns,
            house_reason=row.selection_reason or None,
            shortlist_rank=row.rank,
            has_house_view=True,
        )
    # Not in our shortlist — returns only, no house view (never invent a rationale).
    return FundFacts(
        fund_name=resolved.scheme_name,
        sub_category=None,
        returns=returns,
        house_reason=None,
        shortlist_rank=None,
        has_house_view=False,
    )


async def build_mutual_fund_query_facts(
    db: AsyncSession, resolved: list[ResolvedFund], asked_for: str
) -> MutualFundQueryFacts:
    """Assemble the facts container. A single-fund comparison auto-derives
    like-for-like category peers; 2+ named funds are the comparison set (no peers)."""
    funds = [await _fund_facts_for(db, r) for r in resolved]
    peers: list[PeerFund] = []
    if len(resolved) == 1 and asked_for == "comparison" and funds[0].sub_category:
        for pr in peers_by_sub_category(funds[0].sub_category, exclude_isin=resolved[0].isin or ""):
            p_ret = await trailing_cagr_for_scheme(db, pr.scheme_code) if pr.scheme_code else {}
            peers.append(
                PeerFund(
                    fund_name=pr.fund_name,
                    return_3y_cagr_pct=p_ret.get("return_3y_cagr_pct"),
                    shortlist_rank=pr.rank,
                )
            )
    return MutualFundQueryFacts(funds=funds, peers=peers)


# ---------------------------------------------------------------------------
# Chat gateway
# ---------------------------------------------------------------------------


def _history(ctx) -> list[ConversationTurn]:
    out: list[ConversationTurn] = []
    for d in getattr(ctx, "conversation_history", None) or []:
        role = "user" if d.get("role") == "user" else "assistant"
        out.append(ConversationTurn(role=role, content=d.get("content", "")))
    return out


def _build_held_map(user) -> dict[str, str]:
    """{holding name -> scheme_code} for the customer's MF holdings, so a fund
    they hold resolves to their own scheme (their real plan), not Direct-Growth."""
    held: dict[str, str] = {}
    portfolios = list(getattr(user, "portfolios", []) or [])
    if not portfolios:
        return held
    primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
    for h in getattr(primary, "holdings", []) or []:
        md = getattr(h, "fund_metadata", None)
        name = getattr(h, "instrument_name", None)
        code = getattr(md, "scheme_code", None) if md else None
        if name and code:
            held[name] = code
    return held


_SCREEN_BODY_PROMPT = (
    "The customer asked for the best / top performing mutual funds. FACTS_PACK.screen "
    "holds a ranked shortlist we computed from our fund universe — the top funds by "
    "trailing CAGR over `horizon_years` (Regular plan, Growth option), best first.\n"
    "\n"
    "Write the reply:\n"
    "- Present the funds in FACTS_PACK order (best first), each with its return, and state "
    "the horizon in words (e.g. 'over the last 3 years') and that they're ranked by past return.\n"
    "- If `category` is set, frame them as the best in that category; if null, best overall.\n"
    "- Add one short, honest caveat that past performance doesn't guarantee future returns.\n"
    "- Cite ONLY funds and figures present in FACTS_PACK — never invent a fund or a number.\n"
    "- Keep it tight and scannable: a one-line intro, then the ranked funds."
)


def _screen_facts_pack(
    funds: list[ScreenedFund], horizon_years: int, category: str | None
) -> dict:
    return {
        "screen": {
            "horizon_years": horizon_years,
            "category": category,
            "ranked_funds": [
                {
                    "rank": i,
                    "fund_name": f.scheme_name,
                    "amc": f.amc_name,
                    "sub_category": f.sub_category,
                    "return_cagr_pct": round(f.return_cagr_pct, 2),
                }
                for i, f in enumerate(funds, start=1)
            ],
        }
    }


def _screen_fallback(funds: list[ScreenedFund], horizon_years: int) -> str:
    lines = [f"Top {len(funds)} funds by {horizon_years}-year return:"]
    lines += [
        f"{i}. {f.scheme_name} — {f.return_cagr_pct:.1f}% ({horizon_years}y CAGR)"
        for i, f in enumerate(funds, start=1)
    ]
    lines.append("Past performance doesn't guarantee future returns.")
    return "\n".join(lines)


async def _answer_screen(question: str, ctx, extracted) -> str:
    """No fund named → rank our universe and reply with a tailored top-N shortlist."""
    if getattr(ctx, "db", None) is None:
        return _GENERIC_FAILURE_REPLY
    horizon = extracted.screen_horizon_years or 3
    category = resolve_category(extracted.screen_category)
    try:
        funds = await screen_top_funds(
            ctx.db, horizon_years=horizon, category=category, limit=5
        )
    except Exception:
        logger.exception("mutual_fund_query: screen failed")
        return _GENERIC_FAILURE_REPLY
    if not funds:
        return (
            f"I couldn't find enough funds with a full {horizon}-year track record to "
            "rank right now. Try a different time frame, or ask me about a specific fund."
        )
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=_screen_facts_pack(funds, horizon, category),
        body_prompt=_SCREEN_BODY_PROMPT,
        module_name="mutual_fund_query",
        action_mode="screen",
        profile={"first_name": getattr(getattr(ctx, "user_ctx", None), "first_name", None)},
        build_fallback=lambda: _screen_fallback(funds, horizon),
    )


async def answer_mutual_fund_query(question: str, ctx) -> str:
    """Two-step single-shot: extract fund(s) + ask → resolve → build grounded
    facts → narrate. Returns the customer reply string. A no-fund-named 'best
    performing funds' ask is routed to the screener instead (`_answer_screen`)."""
    try:
        orch = _get_orchestrator()
    except Exception:
        logger.exception("mutual_fund_query: orchestrator init failed")
        return _GENERIC_FAILURE_REPLY

    history = _history(ctx)
    try:
        extracted = await orch.extract(question, history)
    except AnthropicAuthenticationError:
        logger.exception("mutual_fund_query: extract auth error")
        return _GENERIC_FAILURE_REPLY
    except Exception:
        logger.exception("mutual_fund_query: extract failed")
        return _GENERIC_FAILURE_REPLY

    if extracted.is_screen:
        return await _answer_screen(question, ctx, extracted)

    if not extracted.fund_names:
        return "Which fund would you like to know about?"

    if getattr(ctx, "db", None) is None:
        return _GENERIC_FAILURE_REPLY

    held = _build_held_map(getattr(ctx, "user_ctx", None))
    resolved: list[ResolvedFund] = []
    for name in extracted.fund_names:
        r = await resolve_fund(ctx.db, name, held)
        if isinstance(r, Ambiguous):
            opts = " or ".join(r.candidates[:2]) if r.candidates else "which fund"
            return f"Just to be sure — did you mean {opts}?"
        if r is not None:
            resolved.append(r)

    if not resolved:
        return "I couldn't find that fund in our data — could you double-check the name?"

    facts = await build_mutual_fund_query_facts(ctx.db, resolved, extracted.asked_for)
    try:
        resp: MutualFundQueryResponse = await orch.narrate(facts, question, history)
    except AnthropicAuthenticationError:
        logger.exception("mutual_fund_query: narrate auth error")
        return _GENERIC_FAILURE_REPLY
    except Exception:
        logger.exception("mutual_fund_query: narrate failed")
        return _GENERIC_FAILURE_REPLY
    return resp.answer or resp.clarifying_question or _GENERIC_FAILURE_REPLY
