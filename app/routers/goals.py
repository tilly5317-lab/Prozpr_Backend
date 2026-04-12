"""FastAPI router — `goals.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.goals import (
    FinancialGoal,
    GoalContribution,
    GoalHolding,
    GoalPriority,
    GoalStatus,
    GoalType,
)
from app.schemas.goal import (
    GoalContributionCreate,
    GoalContributionResponse,
    GoalCreate,
    GoalDetailResponse,
    GoalHoldingResponse,
    GoalResponse,
    GoalUpdate,
    goal_to_response,
)

router = APIRouter(prefix="/goals", tags=["Goals"])

_LEGACY_STATUS = {
    "ON_TRACK": GoalStatus.ACTIVE,
    "OFF_TRACK": GoalStatus.ACTIVE,
    "CANCELLED": GoalStatus.ABANDONED,
    "ACTIVE": GoalStatus.ACTIVE,
    "ACHIEVED": GoalStatus.ACHIEVED,
    "PAUSED": GoalStatus.PAUSED,
    "ABANDONED": GoalStatus.ABANDONED,
}


async def _goal_totals_map(db: AsyncSession, goal_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[float, float]]:
    if not goal_ids:
        return {}
    inv_stmt = (
        select(GoalContribution.goal_id, func.coalesce(func.sum(GoalContribution.amount), 0))
        .where(GoalContribution.goal_id.in_(goal_ids))
        .group_by(GoalContribution.goal_id)
    )
    inv = {row[0]: float(row[1]) for row in (await db.execute(inv_stmt)).all()}
    line_val = func.coalesce(GoalHolding.current_value, GoalHolding.invested_amount, 0)
    cv_stmt = (
        select(GoalHolding.goal_id, func.coalesce(func.sum(line_val), 0))
        .where(GoalHolding.goal_id.in_(goal_ids))
        .group_by(GoalHolding.goal_id)
    )
    cv = {row[0]: float(row[1]) for row in (await db.execute(cv_stmt)).all()}
    out: dict[uuid.UUID, tuple[float, float]] = {}
    for gid in goal_ids:
        invested = inv.get(gid, 0.0)
        current = cv.get(gid, invested)
        out[gid] = (invested, current)
    return out


@router.get("/", response_model=list[GoalResponse])
async def list_goals(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(FinancialGoal)
        .where(FinancialGoal.user_id == current_user.id)
        .order_by(FinancialGoal.created_at.desc())
    )
    result = await db.execute(stmt)
    goals = list(result.scalars().all())
    totals = await _goal_totals_map(db, [g.id for g in goals])
    out: list[GoalResponse] = []
    for g in goals:
        inv, cur = totals.get(g.id, (0.0, 0.0))
        out.append(goal_to_response(g, invested_amount=inv, current_value=cur))
    return out


@router.post("/", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    try:
        gt = GoalType(payload.goal_type or "OTHER")
    except ValueError:
        gt = GoalType.OTHER
    notes_parts = [payload.notes, payload.description]
    notes = next((p for p in notes_parts if p), None)
    infl = payload.inflation_rate if payload.inflation_rate is not None else 6.0
    td = payload.target_date or (date.today() + timedelta(days=365 * 15))
    goal = FinancialGoal(
        user_id=current_user.id,
        goal_name=payload.name.strip()[:100],
        goal_type=gt,
        present_value_amount=payload.target_amount,
        inflation_rate=infl,
        target_date=td,
        priority=GoalPriority(payload.priority),
        notes=notes,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal_to_response(goal, invested_amount=0.0, current_value=0.0)


@router.get("/{goal_id}", response_model=GoalDetailResponse)
async def get_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(FinancialGoal)
        .options(selectinload(FinancialGoal.contributions), selectinload(FinancialGoal.holdings))
        .where(FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id)
    )
    goal = (await db.execute(stmt)).scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    totals = await _goal_totals_map(db, [goal.id])
    inv, cur = totals.get(goal.id, (0.0, 0.0))
    base = goal_to_response(goal, invested_amount=inv, current_value=cur)
    return GoalDetailResponse(
        **base.model_dump(),
        contributions=[GoalContributionResponse.model_validate(c) for c in goal.contributions],
        holdings=[GoalHoldingResponse.model_validate(h) for h in goal.holdings],
    )


@router.put("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: uuid.UUID,
    payload: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(FinancialGoal).where(
        FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id
    )
    goal = (await db.execute(stmt)).scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        goal.goal_name = str(data.pop("name"))[:100]
    if "target_amount" in data and data["target_amount"] is not None:
        goal.present_value_amount = data.pop("target_amount")
    if "present_value_amount" in data and data["present_value_amount"] is not None:
        goal.present_value_amount = data.pop("present_value_amount")
    if "description" in data:
        desc = data.pop("description")
        if desc is not None:
            goal.notes = desc if not goal.notes else f"{goal.notes}\n{desc}"
    if "slug" in data:
        data.pop("slug", None)
    if "icon" in data:
        data.pop("icon", None)
    if "monthly_contribution" in data:
        data.pop("monthly_contribution", None)
    if "suggested_contribution" in data:
        data.pop("suggested_contribution", None)

    if "goal_type" in data and data["goal_type"]:
        try:
            goal.goal_type = GoalType(data["goal_type"])
        except ValueError:
            pass
        data.pop("goal_type", None)

    if "priority" in data and data["priority"]:
        goal.priority = GoalPriority(data["priority"])
        data.pop("priority", None)

    if "status" in data and data["status"]:
        raw = str(data["status"]).upper()
        if raw in GoalStatus.__members__:
            goal.status = GoalStatus[raw]
        elif raw in _LEGACY_STATUS:
            goal.status = _LEGACY_STATUS[raw]
        data.pop("status", None)

    for field in ("inflation_rate", "target_date", "notes"):
        if field in data:
            setattr(goal, field, data.pop(field))

    await db.commit()
    await db.refresh(goal)
    totals = await _goal_totals_map(db, [goal.id])
    inv, cur = totals.get(goal.id, (0.0, 0.0))
    return goal_to_response(goal, invested_amount=inv, current_value=cur)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(FinancialGoal).where(
        FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id
    )
    goal = (await db.execute(stmt)).scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    await db.delete(goal)
    await db.commit()


@router.post("/{goal_id}/contributions", response_model=GoalContributionResponse, status_code=status.HTTP_201_CREATED)
async def add_contribution(
    goal_id: uuid.UUID,
    payload: GoalContributionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(FinancialGoal).where(
        FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id
    )
    goal = (await db.execute(stmt)).scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    contribution = GoalContribution(
        goal_id=goal_id,
        amount=payload.amount,
        note=payload.note,
    )
    db.add(contribution)
    await db.commit()
    await db.refresh(contribution)
    return GoalContributionResponse.model_validate(contribution)


@router.get("/{goal_id}/contributions", response_model=list[GoalContributionResponse])
async def list_contributions(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(FinancialGoal).where(
        FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id
    )
    if not (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    result = await db.execute(
        select(GoalContribution)
        .where(GoalContribution.goal_id == goal_id)
        .order_by(GoalContribution.contributed_at.desc())
    )
    return [GoalContributionResponse.model_validate(c) for c in result.scalars().all()]


@router.get("/{goal_id}/holdings", response_model=list[GoalHoldingResponse])
async def list_holdings(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(FinancialGoal).where(
        FinancialGoal.id == goal_id, FinancialGoal.user_id == current_user.id
    )
    if not (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    result = await db.execute(select(GoalHolding).where(GoalHolding.goal_id == goal_id))
    return [GoalHoldingResponse.model_validate(h) for h in result.scalars().all()]
