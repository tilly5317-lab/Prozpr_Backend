"""FastAPI router — `portfolio.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.mutual_funds.models.enums import PortfolioSnapshotKind
from app.domains.mutual_funds.models.mf_allocation_snapshot import PortfolioAllocationSnapshot
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioAllocation, PortfolioHistory, PortfolioHolding
from app.domains.ingestion.schemas.finvu import FinvuPortfolioSyncRequest, FinvuPortfolioSyncResponse
from app.domains.portfolio.schemas.portfolio import (
    PortfolioAllocationBulkUpdate,
    PortfolioAllocationResponse,
    PortfolioDetailResponse,
    PortfolioHistoryResponse,
    PortfolioHoldingResponse,
    PortfolioNavHistoryPoint,
    PortfolioNavHistoryResponse,
    PortfolioResponse,
    RecommendedPlanResponse,
    RecommendedPlanSnapshotResponse,
)
from app.domains.ingestion.services.finvu_portfolio_sync import apply_finvu_bucket_snapshot
from app.domains.profile.services._effective_risk import maybe_recalculate_effective_risk
from app.domains.portfolio.services.portfolio_service import get_or_create_primary_portfolio
from app.domains.portfolio.services.nav_history_service import (
    HORIZON_DAYS,
    get_user_nav_history,
    recompute_user_nav_history,
)

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/recommended-plan", response_model=RecommendedPlanResponse)
async def get_recommended_plan(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """
    Latest ideal allocation produced by chat (when the allocation engine is active).

    Returns the IDEAL ``portfolio_allocation_snapshots`` row (class mix + full
    pipeline output) and the matching ``asset_allocation_runs`` row id (ORM:
    ``AssetAllocationRun`` under ``app.models.asset_allocation``) for approval flows.
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

    run_stmt = (
        select(AssetAllocationRun)
        .where(
            AssetAllocationRun.user_id == uid,
            AssetAllocationRun.spine_mode == "ideal_asset_allocation",
        )
        .order_by(AssetAllocationRun.created_at.desc())
        .limit(1)
    )
    latest_run = (await db.execute(run_stmt)).scalar_one_or_none()

    return RecommendedPlanResponse(
        snapshot=RecommendedPlanSnapshotResponse.model_validate(snap) if snap else None,
        latest_asset_allocation_run_id=latest_run.id if latest_run else None,
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


@router.get("/nav-history", response_model=PortfolioNavHistoryResponse)
async def get_nav_history(
    horizon: str = Query(default="1Y", pattern="^(?i)(1M|1Y|3Y|MAX)$"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Daily per-user portfolio value series for the dashboard chart.

    Computed from the user's holdings (units × backcast NAV) and cached in
    ``user_portfolio_nav_history``. Re-runs are idempotent.
    """
    horizon_norm = horizon.upper()
    rows = await get_user_nav_history(db, current_user.id, horizon=horizon_norm)
    points = [PortfolioNavHistoryPoint.model_validate(r) for r in rows]
    invested = float(rows[-1].total_invested) if rows else 0.0
    current = float(rows[-1].total_value) if rows else 0.0
    gain_pct = float(rows[-1].gain_percentage) if rows else 0.0
    return PortfolioNavHistoryResponse(
        horizon=horizon_norm,
        points=points,
        total_invested=invested,
        current_value=current,
        gain_percentage=gain_pct,
    )


@router.post("/nav-history/refresh", response_model=PortfolioNavHistoryResponse)
async def refresh_nav_history(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Force a recompute of the user's daily NAV history (MAX window)."""
    await recompute_user_nav_history(db, current_user.id, days=HORIZON_DAYS["MAX"])
    rows = await get_user_nav_history(db, current_user.id, horizon="MAX")
    points = [PortfolioNavHistoryPoint.model_validate(r) for r in rows]
    invested = float(rows[-1].total_invested) if rows else 0.0
    current = float(rows[-1].total_value) if rows else 0.0
    gain_pct = float(rows[-1].gain_percentage) if rows else 0.0
    return PortfolioNavHistoryResponse(
        horizon="MAX",
        points=points,
        total_invested=invested,
        current_value=current,
        gain_percentage=gain_pct,
    )


@router.post("/finvu/sync", response_model=FinvuPortfolioSyncResponse, deprecated=True)
async def sync_finvu_bucket_portfolio(
    payload: FinvuPortfolioSyncRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """
    DEPRECATED — the Finvu account-aggregator integration is paused (licensing).

    Kept for reference / backwards compatibility only. To bring in a user's
    mutual-fund holdings and transactions, upload a CAMS / KFintech Consolidated
    Account Statement PDF via ``POST /api/v1/mf-ingest/cams-pdf`` instead.

    Ingest Finvu / AA consolidated bucket totals into the primary portfolio.
    Uses the same **Cash / Debt / Equity / Other** asset_class labels as SimBanks sync so
    chat, drift, and allocation modules read a single canonical shape from the DB.
    """
    out = await apply_finvu_bucket_snapshot(db, current_user.id, payload)
    await db.commit()
    await maybe_recalculate_effective_risk(db, current_user.id, "finvu_portfolio_sync")
    await db.commit()
    return out
