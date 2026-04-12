"""FastAPI router — `rebalancing.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.portfolio import Portfolio
from app.models.rebalancing import RebalancingRecommendation, RebalancingStatus
from app.schemas.rebalancing import RebalancingResponse, RebalancingStatusUpdate

router = APIRouter(prefix="/rebalancing", tags=["Rebalancing"])


@router.get("/", response_model=list[RebalancingResponse])
async def list_recommendations(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRecommendation)
        .join(Portfolio, Portfolio.id == RebalancingRecommendation.portfolio_id)
        .where(Portfolio.user_id == current_user.id)
        .order_by(RebalancingRecommendation.created_at.desc())
    )
    result = await db.execute(stmt)
    return [RebalancingResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/{recommendation_id}", response_model=RebalancingResponse)
async def get_recommendation(
    recommendation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRecommendation)
        .join(Portfolio, Portfolio.id == RebalancingRecommendation.portfolio_id)
        .where(
            RebalancingRecommendation.id == recommendation_id,
            Portfolio.user_id == current_user.id,
        )
    )
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    return RebalancingResponse.model_validate(rec)


@router.put("/{recommendation_id}/status", response_model=RebalancingResponse)
async def update_status(
    recommendation_id: uuid.UUID,
    payload: RebalancingStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRecommendation)
        .join(Portfolio, Portfolio.id == RebalancingRecommendation.portfolio_id)
        .where(
            RebalancingRecommendation.id == recommendation_id,
            Portfolio.user_id == current_user.id,
        )
    )
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")

    rec.status = RebalancingStatus(payload.status)
    await db.commit()
    await db.refresh(rec)
    return RebalancingResponse.model_validate(rec)
