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

import logging

from app.domains.ai_engine.types import AIModule, ModuleOutput

logger = logging.getLogger(__name__)


async def flow_asset_allocation(turn, ctx) -> ModuleOutput:
    from app.domains.asset_allocation.services.asset_allocation_module_service import (
        run as run_asset_allocation,
    )

    return await run_asset_allocation(turn, ctx, {})


async def flow_rebalancing(turn, ctx) -> ModuleOutput:
    # Practical (holdings-aware) asset allocation first, then rebalance to it.
    # Practical allocation replaces the ideal asset_allocation in this flow; it
    # takes the asset-allocation slot, so the rebalancing domain still reads the
    # target from ``prior[ASSET_ALLOCATION]`` rather than computing its own.
    from app.domains.practical_asset_allocation.services.practical_asset_allocation_module_service import (
        run as run_practical_asset_allocation,
    )
    from app.domains.rebalancing.services.rebalancing_module_service import (
        run as run_rebalancing,
    )

    paa = await run_practical_asset_allocation(turn, ctx, {})
    return await run_rebalancing(turn, ctx, {AIModule.ASSET_ALLOCATION.value: paa})


async def flow_goal_planning(turn, ctx) -> ModuleOutput:
    from app.domains.cashflow.services.cashflow_module_service import (
        run as run_cashflow,
    )

    return await run_cashflow(turn, ctx, {})


async def flow_portfolio_query(turn, ctx) -> ModuleOutput:
    # Read-only: answer from the user's current portfolio. No persistence.
    from app.domains.portfolio.services.portfolio_query_service import (
        answer_portfolio_query,
    )

    return ModuleOutput(text=await answer_portfolio_query(turn.user_question, ctx))


async def flow_market(turn, ctx) -> ModuleOutput:
    # Market commentary produces a macro doc; general_chat tailors the reply to
    # it (read from ``prior[MARKET_COMMENTARY]``).
    from app.domains.general_chat.services.general_chat_module_service import (
        run as run_general_chat,
    )
    from app.domains.market_commentary.services.market_commentary_module_service import (
        run as run_market_commentary,
    )

    macro = await run_market_commentary(turn, ctx, {})
    return await run_general_chat(turn, ctx, {AIModule.MARKET_COMMENTARY.value: macro})


async def flow_general_chat(turn, ctx) -> ModuleOutput:
    from app.domains.general_chat.services.general_chat_module_service import (
        run as run_general_chat,
    )

    return await run_general_chat(turn, ctx, {})


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
    "goal_planning": flow_goal_planning,
    "general_market_query": flow_market,
}

# When the cashflow save-flow is open from a prior turn, the brain routes here
# regardless of this turn's classified intent.
AWAITING_SAVE_FLOW = flow_goal_planning
