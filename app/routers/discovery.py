"""FastAPI router — `discovery.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.fund import Fund
from app.schemas.discovery import FundListResponse, FundResponse, SectorResponse

router = APIRouter(prefix="/discovery", tags=["Discovery"])


@router.get("/funds", response_model=FundListResponse)
async def list_funds(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
):
    scope_filter = (Fund.user_id == current_user.id) | (Fund.user_id.is_(None))
    stmt = select(Fund).where(scope_filter)
    count_stmt = select(func.count(Fund.id)).where(scope_filter)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Fund.name.ilike(pattern) | Fund.ticker_symbol.ilike(pattern))
        count_stmt = count_stmt.where(Fund.name.ilike(pattern) | Fund.ticker_symbol.ilike(pattern))
    if category:
        stmt = stmt.where(Fund.category == category)
        count_stmt = count_stmt.where(Fund.category == category)
    if sector:
        stmt = stmt.where(Fund.sector == sector)
        count_stmt = count_stmt.where(Fund.sector == sector)

    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.offset(offset).limit(limit))
    funds = [FundResponse.model_validate(f) for f in result.scalars().all()]
    return FundListResponse(funds=funds, total=total)


@router.get("/funds/{fund_id}", response_model=FundResponse)
async def get_fund(
    fund_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Fund).where(
        Fund.id == fund_id,
        (Fund.user_id == current_user.id) | (Fund.user_id.is_(None)),
    )
    fund = (await db.execute(stmt)).scalar_one_or_none()
    if not fund:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund not found")
    return FundResponse.model_validate(fund)


@router.get("/sectors", response_model=list[SectorResponse])
async def list_sectors(
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Fund.sector, func.count(Fund.id).label("fund_count"))
        .where(
            Fund.sector.isnot(None),
            (Fund.user_id == current_user.id) | (Fund.user_id.is_(None)),
        )
        .group_by(Fund.sector)
        .order_by(func.count(Fund.id).desc())
    )
    result = await db.execute(stmt)
    return [SectorResponse(sector=row.sector, fund_count=row.fund_count) for row in result.all()]


@router.get("/trending", response_model=list[FundResponse])
async def get_trending_funds(
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Fund)
        .where(
            Fund.is_trending == True,
            (Fund.user_id == current_user.id) | (Fund.user_id.is_(None)),
        )
        .limit(10)
    )
    result = await db.execute(stmt)
    return [FundResponse.model_validate(f) for f in result.scalars().all()]


@router.get("/house-view", response_model=list[FundResponse])
async def get_house_view_funds(
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Fund)
        .where(
            Fund.is_house_view == True,
            (Fund.user_id == current_user.id) | (Fund.user_id.is_(None)),
        )
        .limit(10)
    )
    result = await db.execute(stmt)
    return [FundResponse.model_validate(f) for f in result.scalars().all()]
