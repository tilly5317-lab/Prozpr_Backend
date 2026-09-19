"""Run cashflow projection via the deterministic engine (no LangGraph)."""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.cashflow.services.goal_planning_engine.cashflow_trace import (
    log_output,
    log_skipped,
    log_trigger,
)
from app.domains.cashflow.services.goal_planning_engine.input_builder import (
    build_goal_planning_input_for_user,
)


async def run_cashflow_projection_for_user(
    user: User,
    *,
    anchor_date: date | None = None,
    detail_level: str = "full",
    portfolio_value: float | None = None,
    db: AsyncSession | None = None,
):
    """Build input from DB, run ``compute_full_projection``, return a snapshot.

    ``portfolio_value`` (the portfolio page's Current Net Worth Value) is folded
    into the engine's starting corpus alongside cash & assets. When it is not
    passed but ``db`` is, it is resolved here, so no caller silently falls back
    to the manually-entered corpus.
    """
    ensure_ai_agents_path()
    from cashflow_statement.engine import compute_full_projection
    from cashflow_statement.models import GoalPlanningSnapshot

    if portfolio_value is None and db is not None:
        from app.domains.portfolio.services.portfolio_service import (
            get_current_net_worth_value,
        )

        portfolio_value = await get_current_net_worth_value(db, user.id)

    anchor = anchor_date or date.today()
    log_trigger(path="rest", user_id=user.id, reason=f"detail_level={detail_level}")
    try:
        inp, _debug = build_goal_planning_input_for_user(
            user, anchor, portfolio_value=portfolio_value
        )
    except ValueError as e:
        log_skipped(path="rest", user_id=user.id, error=str(e))
        raise
    if detail_level != "full":
        inp = inp.model_copy(update={"detail_level": detail_level})

    out = await asyncio.to_thread(compute_full_projection, inp)
    log_output(path="rest", user_id=user.id, output=out)
    return GoalPlanningSnapshot(**out.model_dump())
