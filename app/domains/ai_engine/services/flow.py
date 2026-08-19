"""Per-intent flows — the ONE place where domains are composed into a sequence.

The brain has already classified the turn; a *flow* is the explicit recipe for
that intent. It calls one or more **domain** functions in order and returns a
``ModuleOutput`` (final reply text + any persisted ids / chart payloads) for the
brain to ship.

The mental model, made literal:

    async def flow_rebalancing(turn, ctx):
        paa = await run_practical_asset_allocation(turn, ctx)  # practical_asset_allocation domain
        reb = await run_rebalancing(turn, ctx, prior=paa)      # rebalancing domain
        return reb                                             # rebalancing owns the reply

Rules:
  - Each domain exposes ONE callable the flow uses: its
    ``<domain>/services/<domain>_module_service.run(turn, ctx, prior)`` (or, for
    portfolio, the read-only ``answer_portfolio_query``). That ``run`` is the
    domain's public entry; everything it needs (input building, user-context
    fetch, the AI_Agents call, persistence, formatting) happens INSIDE the
    domain.
  - A flow may call several domains, but a domain never calls another domain.
    Cross-domain data (e.g. the allocation target rebalancing rebalances to) is
    produced by one domain and handed to the next via the ``prior`` dict — never
    fetched by reaching into another domain.

All domain imports are lazy (inside the flow) so importing this module never
pulls a heavy agent package at app boot.
"""

from __future__ import annotations

import dataclasses
import logging

from app.domains.ai_engine.thinking import publish_turn_thinking as _think
from app.domains.ai_engine.types import AIModule, ModuleOutput

logger = logging.getLogger(__name__)


async def flow_asset_allocation(turn, ctx) -> ModuleOutput:
    from app.domains.asset_allocation.services.asset_allocation_module_service import (
        run as run_asset_allocation,
    )

    _think(turn, 42, "Designing your ideal asset mix from your risk profile…")
    return await run_asset_allocation(turn, ctx, {})


async def flow_rebalancing(turn, ctx) -> ModuleOutput:
    # Practical (holdings-aware) asset allocation first, then rebalance to it.
    # COUPLING IS VIA THE DB, NOT ``prior``: the PAA step PERSISTS a fresh
    # allocation run, and the rebalancing engine's cache-first lookup (90-day
    # TTL) reads that run as its target. The ``prior`` dict passed below is
    # informational only — rebalancing_module_service does not consume it.
    # Removing the PAA step would NOT save compute; it would silently
    # rebalance against a stale (up to 90 days old) allocation.
    from app.domains.practical_asset_allocation.services.practical_asset_allocation_module_service import (
        run as run_practical_asset_allocation,
    )
    from app.domains.rebalancing.services.rebalancing_module_service import (
        run as run_rebalancing,
    )

    _think(turn, 38, "Designing your personalised target asset mix…")
    paa = await run_practical_asset_allocation(turn, ctx, {})
    _think(
        turn,
        68,
        "Comparing your current holdings with the target and drafting trades…",
    )
    return await run_rebalancing(turn, ctx, {AIModule.ASSET_ALLOCATION.value: paa})


async def flow_financial_planning(turn, ctx) -> ModuleOutput:
    """The customer's plan: their figures, their goals, and the projection over both.

    One flow where there were two, because the customer's turn is one thing.
    "My income is up 20% — am I still on track?" used to be a routing coin-flip
    between a profile update and a goal-planning question; here it is a single
    read that produces a write and a projection, in that order.

    The shape:

      1. The planning module reads the message, stages what it asks for, and
         replies. Nothing is written without a yes.
      2. If it says the projection is now owed — because a plan input or a goal
         ACTUALLY changed, or because the customer asked — run the engine. If
         nothing that feeds the projection moved, the engine is not run at all:
         re-deriving an identical answer is pure cost.
      3. Before running it, apply the projection's own input requirement. This
         is deliberately NOT applied at the top: the intent covers CREATING a
         goal, which needs a cost and a date and nothing else, and TESTING the
         plan, which does need their income. Gating at the top asked a customer
         for their salary before it would let them describe a wedding.
      4. If the answer unblocked another intent, run that instead — answering a
         question we asked should cost the customer nothing.
    """
    from app.domains.financial_planning.services.planning_module_service import (
        run as run_planning,
    )

    _think(turn, 25, "Working out what you're planning for…")
    out = await run_planning(turn, ctx, {})
    side = dict(out.side_effects or {})

    if side.get("handoff"):
        # Read with the open thread in view, this turn turned out not to be
        # about the plan at all. Whoever can answer it should.
        logger.info("financial_planning handed the turn back to general chat")
        return await flow_general_chat(turn, ctx)

    if side.get("run_projection"):
        return await _project(turn, ctx, side)

    resume = side.get("resume_intent")
    if not resume:
        return out

    resumed_flow = FLOWS.get(resume)
    if resumed_flow is None or resumed_flow is flow_financial_planning:
        return out

    logger.info("financial_planning cleared the block; resuming intent=%r", resume)
    _think(turn, 55, "Thanks — that's everything I needed. Working on your answer…")

    # Re-run the engine against the question the customer ACTUALLY asked.
    # Replaying this turn's message ("5+ years") through the engine made it ask
    # what "5+ years" referred to, because the engine and the formatter both
    # read user_question as the thing being answered.
    origin = side.get("resume_question")
    if origin and origin != turn.user_question:
        turn = dataclasses.replace(turn, user_question=origin)
        ctx = dataclasses.replace(ctx, user_question=origin)

    resumed = await resumed_flow(turn, ctx)
    resumed.side_effects = _carry_chips(side, resumed.side_effects)
    return resumed


async def _project(turn, ctx, side) -> ModuleOutput:
    """Run the cashflow / goal projection, or ask for the one input it needs."""
    import dataclasses as _dc

    from app.domains.ai_engine.planning_gate import (
        PlanningDirective,
        next_blocking_field,
    )
    from app.domains.cashflow.services.cashflow_module_service import run as run_cashflow
    from app.domains.financial_planning.services.planning_module_service import (
        PROJECTION_REQUIREMENT,
        run as run_planning,
    )

    if ctx.db is not None and ctx.session_id is not None:
        try:
            blocking = await next_blocking_field(
                ctx.db, ctx.effective_user_id, ctx.session_id, PROJECTION_REQUIREMENT
            )
        except Exception:
            logger.exception("projection input check failed; running the engine")
            blocking = None
        if blocking:
            _think(turn, 35, "Checking what I still need to run your projection…")
            asked = await run_planning(
                turn,
                _dc.replace(
                    ctx,
                    planning_directive=PlanningDirective(
                        field_key=blocking, resume_intent="financial_planning"
                    ),
                ),
                {},
            )
            asked.side_effects = _carry_chips(side, asked.side_effects)
            return asked

    turn, ctx = await _refresh_user_graph(turn, ctx, side)
    _think(turn, 60, "Running your cashflow and goal projections…")
    projection = await run_cashflow(turn, ctx, {})
    projection.side_effects = _carry_chips(side, projection.side_effects)
    return projection



async def _refresh_user_graph(turn, ctx, side):
    """Re-read the customer's graph when this turn already wrote to it.

    The engine runs off the graph loaded at the START of the turn. A profile
    column written since then is still visible — the write mutated the very
    object the graph holds. A GOAL is not: adding one never appends to an
    already-loaded ``user.financial_goals``, and deleting one never removes it.
    Projecting on that graph reports a verdict computed without the goal the
    customer just added, which is the one thing they were asking about.

    Only runs when something WAS written, and fails soft — an unrefreshed
    projection is worse than a fresh one, but far better than no answer.
    """
    import dataclasses as _dc

    wrote = any(
        side.get(k) for k in ("planning_saved", "goal_saved", "goal_removed")
    )
    if not wrote or ctx.db is None or ctx.effective_user_id is None:
        return turn, ctx
    try:
        from app.domains.identity.services.user_context_loader import load_user_for_ai

        user = await load_user_for_ai(ctx.db, ctx.effective_user_id, refresh=True)
    except Exception:
        logger.exception(
            "could not refresh the user graph after a write; "
            "projecting on the preloaded one"
        )
        return turn, ctx
    if user is None:
        return turn, ctx
    logger.info("refreshed the user graph before projecting (this turn wrote)")
    return _dc.replace(turn, user_ctx=user), _dc.replace(ctx, user_ctx=user)


def _carry_chips(side, onto) -> dict:
    """Carry what the planning turn changed onto whatever answers it.

    The saved / removed chips belong to the message the customer sees, and the
    message they see is the projection's — so they have to survive the handover
    or the write happens invisibly.
    """
    merged = dict(onto or {})
    for key in ("planning_saved", "planning_noted", "goal_saved", "goal_removed"):
        if side.get(key):
            merged[key] = side[key]
    return merged


async def flow_portfolio_query(turn, ctx) -> ModuleOutput:
    # Read-only: answer from the user's current portfolio. No persistence.
    from app.domains.portfolio.services.portfolio_query_service import (
        answer_portfolio_query,
    )

    _think(turn, 45, "Looking through your portfolio holdings…")
    return ModuleOutput(text=await answer_portfolio_query(turn.user_question, ctx))


async def flow_mutual_fund_query(turn, ctx) -> ModuleOutput:
    # Read-only: answer about a specific fund from our ranking CSV + stored NAV.
    # No persistence; grounded facts are built inside the domain service.
    from app.domains.mutual_funds.services.mutual_fund_query_service import answer_mutual_fund_query

    _think(turn, 45, "Pulling this fund's data and our research view on it…")
    return ModuleOutput(text=await answer_mutual_fund_query(turn.user_question, ctx))


async def flow_market(turn, ctx) -> ModuleOutput:
    # Market commentary produces a macro doc; general_chat tailors the reply to
    # it (read from ``prior[MARKET_COMMENTARY]``).
    from app.domains.general_chat.services.general_chat_module_service import (
        run as run_general_chat,
    )
    from app.domains.market_commentary.services.market_commentary_module_service import (
        run as run_market_commentary,
    )

    _think(turn, 40, "Reviewing today's market context…")
    macro = await run_market_commentary(turn, ctx, {})
    _think(turn, 72, "Writing your answer against that market view…")
    return await run_general_chat(turn, ctx, {AIModule.MARKET_COMMENTARY.value: macro})


async def flow_general_chat(turn, ctx) -> ModuleOutput:
    from app.domains.general_chat.services.general_chat_module_service import (
        run as run_general_chat,
    )

    _think(turn, 50, "Researching and composing your answer…")
    return await run_general_chat(turn, ctx, {})


async def flow_additional_investment(turn, ctx) -> ModuleOutput:
    # Deploy fresh money (lumpsum/SIP) into specific funds. Unlike flow_rebalancing,
    # this flow does NOT pre-run practical asset allocation: the additional_investment
    # orchestrator self-primes PAA inside its own run (it needs the persisted
    # allocation RUN for source_allocation_run_id, not just the targets), so
    # pre-running it here would compute the allocation twice.
    from app.domains.additional_investment.services.additional_investment_module_service import (
        run as run_additional_investment,
    )

    _think(turn, 42, "Working out how to deploy your money across funds…")
    return await run_additional_investment(turn, ctx, {})


# ---------------------------------------------------------------------------
# The switch: intent name -> flow. Unknown intents fall back to general_chat.
# Adding/altering an intent = edit one row here + its flow above. The brain
# never changes.
# ---------------------------------------------------------------------------

FLOWS = {
    "asset_allocation": flow_asset_allocation,
    "portfolio_query": flow_portfolio_query,
    "general_chat": flow_general_chat,
    "rebalancing": flow_rebalancing,
    "financial_planning": flow_financial_planning,
    "general_market_query": flow_market,
    "additional_investment": flow_additional_investment,
    "mutual_fund_query": flow_mutual_fund_query,
}
