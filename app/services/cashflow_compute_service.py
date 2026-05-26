"""Run cashflow projection via the deterministic engine (no LangGraph)."""

from __future__ import annotations

import asyncio
from datetime import date

from app.models.user import User
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.ai_bridge.goal_planning.input_builder import (
    build_goal_planning_input_for_user,
)


async def run_cashflow_projection_for_user(
    user: User,
    *,
    anchor_date: date | None = None,
    detail_level: str = "full",
):
    """Build input from DB, run ``compute_full_projection``, return a snapshot."""
    ensure_ai_agents_path()
    from cashflow_statement.engine import compute_full_projection
    from cashflow_statement.models import GoalPlanningSnapshot

    anchor = anchor_date or date.today()
    inp, _debug = build_goal_planning_input_for_user(user, anchor)
    if detail_level != "full":
        inp = inp.model_copy(update={"detail_level": detail_level})

    out = await asyncio.to_thread(compute_full_projection, inp)
    return GoalPlanningSnapshot(**out.model_dump())
