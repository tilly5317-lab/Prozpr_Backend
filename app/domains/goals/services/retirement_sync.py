"""Keep the Retirement goal's target year and the investment profile's
``retirement_age`` in lockstep — one fact (when the user retires), two views.

The goal's target year is the canonical statement; ``retirement_age`` is its
age-denominated mirror. Whichever side the user edits, the other follows:

- goal create/update (``goals_router``) → ``sync_retirement_age_from_goal``
  writes ``retirement_age = target year − birth year``;
- ``PUT /profile/investment`` with a retirement_age (``profile_router``) →
  ``sync_retirement_goal_from_age`` moves the active Retirement goal to
  ``birth year + age`` (July 1 — the same anchor the frontend timeline uses).

Both writers already call ``mark_cashflow_stale`` afterwards, so the plan run
recomputes on the synced values.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.goals.models import FinancialGoal, GoalStatus
from app.domains.identity.models.user import User
from app.domains.profile.models.investment_profile import InvestmentProfile


def is_retirement_goal(goal: Any) -> bool:
    """Retirement when goal_type is RETIREMENT or the name contains "retire" —
    quick-pick goals are stored with goal_type=OTHER/NULL, so the name is the
    reliable signal (same rule as the cashflow readiness/input builder)."""
    gt = getattr(goal, "goal_type", None)
    gt_name = (gt.value if hasattr(gt, "value") else str(gt or "")).upper()
    name = str(getattr(goal, "name", None) or getattr(goal, "goal_name", None) or "")
    return gt_name == "RETIREMENT" or "retire" in name.casefold()


async def _birth_date(db: AsyncSession, user_id: uuid.UUID) -> Optional[date]:
    return (
        await db.execute(select(User.date_of_birth).where(User.id == user_id))
    ).scalar_one_or_none()


async def sync_retirement_age_from_goal(
    db: AsyncSession, user_id: uuid.UUID, goal: FinancialGoal
) -> None:
    """Goal → profile: after a retirement goal is created/moved, persist
    ``retirement_age = target year − birth year``. No-op for non-retirement
    goals, missing DOB, or when already in sync. Commits on change."""
    if not is_retirement_goal(goal):
        return
    target = getattr(goal, "target_date", None) or getattr(goal, "goal_date", None)
    if target is None:
        return
    dob = await _birth_date(db, user_id)
    if dob is None:
        return
    age = max(1, target.year - dob.year)
    profile = (
        await db.execute(
            select(InvestmentProfile).where(InvestmentProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        db.add(InvestmentProfile(user_id=user_id, retirement_age=age))
    elif profile.retirement_age == age:
        return
    else:
        profile.retirement_age = age
    await db.commit()


async def sync_retirement_goal_from_age(
    db: AsyncSession, user_id: uuid.UUID, retirement_age: int
) -> None:
    """Profile → goal: after the user saves a retirement age, move the earliest
    ACTIVE Retirement goal (the one the timeline/engine anchor on) to
    ``birth year + age``, July 1. No-op without a DOB or a retirement goal, or
    when already in sync. Commits on change."""
    dob = await _birth_date(db, user_id)
    if dob is None:
        return
    goals = (
        (
            await db.execute(
                select(FinancialGoal).where(
                    FinancialGoal.user_id == user_id,
                    FinancialGoal.status == GoalStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )
    retirement_goals = [g for g in goals if is_retirement_goal(g)]
    if not retirement_goals:
        return
    goal = min(
        retirement_goals,
        key=lambda g: g.target_date or g.goal_date or date.max,
    )
    new_date = date(dob.year + int(retirement_age), 7, 1)
    if goal.target_date == new_date:
        return
    goal.target_date = new_date
    goal.goal_date = new_date
    await db.commit()
