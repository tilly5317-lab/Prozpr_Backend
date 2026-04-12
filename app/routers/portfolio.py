"""FastAPI router — `portfolio.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.models.portfolio import Portfolio, PortfolioAllocation, PortfolioHistory, PortfolioHolding
from app.models.rebalancing import RebalancingRecommendation
from app.schemas.ingest.finvu import FinvuPortfolioSyncRequest, FinvuPortfolioSyncResponse
from app.schemas.portfolio import (
    PortfolioAllocationBulkUpdate,
    PortfolioAllocationResponse,
    PortfolioDetailResponse,
    PortfolioHistoryResponse,
    PortfolioHoldingResponse,
    PortfolioResponse,
    RecommendedPlanResponse,
    RecommendedPlanSnapshotResponse,
)
from app.services.finvu_portfolio_sync import apply_finvu_bucket_snapshot
from app.services.effective_risk_profile import maybe_recalculate_effective_risk
from app.services.portfolio_service import get_or_create_primary_portfolio

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/recommended-plan", response_model=RecommendedPlanResponse)
async def get_recommended_plan(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """
    Latest ideal allocation produced by chat or ``/ai-modules/asset-allocation/recommend``.

    Includes the JSON snapshot (class mix + full ``ideal_allocation_output``) and, when
    present, the matching ``rebalancing_recommendations`` row id for approval flows.
    """
    uid = current_user.id
    snap_stmt = (
        select(PortfolioAllocationSnapshot)
        .where(
            PortfolioAllocationSnapshot.user_id == uid,
            PortfolioAllocationSnapshot.snapshot_kind == PortfolioSnapshotKind.IDEAL,
            PortfolioAllocationSnapshot.source == "ideal_asset_allocation",
        )
        .order_by(PortfolioAllocationSnapshot.effective_at.desc())
        .limit(1)
    )
    snap = (await db.execute(snap_stmt)).scalar_one_or_none()

    reb_stmt = (
        select(RebalancingRecommendation)
        .join(Portfolio, Portfolio.id == RebalancingRecommendation.portfolio_id)
        .where(Portfolio.user_id == uid)
        .order_by(RebalancingRecommendation.created_at.desc())
        .limit(15)
    )
    rebs = (await db.execute(reb_stmt)).scalars().all()
    latest_ideal = next(
        (
            r
            for r in rebs
            if (r.recommendation_data or {}).get("source") == "ideal_asset_allocation"
        ),
        None,
    )

    return RecommendedPlanResponse(
        snapshot=RecommendedPlanSnapshotResponse.model_validate(snap) if snap else None,
        latest_rebalancing_id=latest_ideal.id if latest_ideal else None,
    )


@router.get("/", response_model=PortfolioDetailResponse)
async def get_portfolio(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.allocations),
            selectinload(Portfolio.holdings),
        )
        .where(Portfolio.user_id == current_user.id, Portfolio.is_primary == True)
    )
    portfolio = (await db.execute(stmt)).scalar_one_or_none()
    if not portfolio:
        portfolio = await get_or_create_primary_portfolio(db, current_user.id)
        await db.commit()
        await db.refresh(portfolio)
        return PortfolioDetailResponse(
            **PortfolioResponse.model_validate(portfolio).model_dump(),
            allocations=[],
            holdings=[],
        )

    return PortfolioDetailResponse(
        **PortfolioResponse.model_validate(portfolio).model_dump(),
        allocations=[PortfolioAllocationResponse.model_validate(a) for a in portfolio.allocations],
        holdings=[PortfolioHoldingResponse.model_validate(h) for h in portfolio.holdings],
    )


@router.put("/allocations", response_model=list[PortfolioAllocationResponse])
async def update_allocations(
    payload: PortfolioAllocationBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)

    await db.execute(
        delete(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id)
    )

    allocations = []
    for item in payload.allocations:
        alloc = PortfolioAllocation(
            portfolio_id=portfolio.id,
            asset_class=item.asset_class,
            allocation_percentage=item.allocation_percentage,
            amount=item.amount,
        )
        db.add(alloc)
        allocations.append(alloc)

    if payload.total_investment is not None:
        portfolio.total_invested = payload.total_investment
        portfolio.total_value = payload.total_investment

    await db.commit()
    for a in allocations:
        await db.refresh(a)
    await maybe_recalculate_effective_risk(db, current_user.id, "portfolio_allocation_update")
    await db.commit()
    return [PortfolioAllocationResponse.model_validate(a) for a in allocations]


@router.get("/allocations", response_model=list[PortfolioAllocationResponse])
async def get_allocations(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)
    result = await db.execute(
        select(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id)
    )
    return [PortfolioAllocationResponse.model_validate(a) for a in result.scalars().all()]


@router.get("/holdings", response_model=list[PortfolioHoldingResponse])
async def get_holdings(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)
    result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id)
    )
    return [PortfolioHoldingResponse.model_validate(h) for h in result.scalars().all()]


@router.get("/history", response_model=list[PortfolioHistoryResponse])
async def get_history(
    limit: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)
    result = await db.execute(
        select(PortfolioHistory)
        .where(PortfolioHistory.portfolio_id == portfolio.id)
        .order_by(PortfolioHistory.recorded_date.desc())
        .limit(limit)
    )
    return [PortfolioHistoryResponse.model_validate(h) for h in result.scalars().all()]


@router.post("/finvu/sync", response_model=FinvuPortfolioSyncResponse)
async def sync_finvu_bucket_portfolio(
    payload: FinvuPortfolioSyncRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """
    Ingest Finvu / AA consolidated bucket totals into the primary portfolio.

    Uses the same **Cash / Debt / Equity / Other** asset_class labels as SimBanks sync so
    chat, drift, and allocation modules read a single canonical shape from the DB.
    """
    out = await apply_finvu_bucket_snapshot(db, current_user.id, payload)
    await db.commit()
    await maybe_recalculate_effective_risk(db, current_user.id, "finvu_portfolio_sync")
    await db.commit()
    return out
