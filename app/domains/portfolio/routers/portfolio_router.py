"""FastAPI router — `portfolio.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
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
from app.domains.mutual_funds.services.scheme_classification import (
    classify_holding,
    resolve_asset_bucket,
)
from app.domains.portfolio.schemas.portfolio import (
    NetworthJobStatusResponse,
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
from app.domains.portfolio.services.portfolio_service import (
    get_or_create_primary_portfolio,
    revalue_primary_portfolio_at_latest_nav,
)
from app.domains.portfolio.services.nav_history_service import (
    get_user_nav_history,
)
from app.domains.portfolio.services.networth_history_service import (
    compute_user_networth_history,
    create_job,
    get_latest_job,
    has_running_job,
    run_networth_backfill,
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


def _build_holding_response(holding: PortfolioHolding) -> PortfolioHoldingResponse:
    """Serialize a holding with the canonical 3-bucket asset_class + SEBI sub_category.

    asset_class uses the same vocabulary as PortfolioAllocation
    (``Equity`` / ``Debt`` / ``Others``) — the donut, holdings list, and target
    view all agree. Bucket is recomputed at response time via the canonical
    classifier; per-holding bucket isn't persisted.

    Resolution order:
      1. ``classify_holding(sub_category, name)`` — SEBI sub_category lookup
         plus the ``arbitrage_plus_income`` name override.
      2. Direct equity shortcut — non-MF holdings with ``instrument_type``
         in {"equity", "stock", "share"} are equities by definition.
      3. Fall back to ``resolve_asset_bucket(sub_category | category, name)`` —
         name-based last-resort for funds whose metadata is absent.
    """
    md = holding.fund_metadata
    sebi_sub = md.sub_category if md else None
    sebi_cat = md.category if md else None
    asset_class, _ = classify_holding(sebi_sub, holding.instrument_name)
    if asset_class is None:
        itype = (holding.instrument_type or "").strip().lower()
        if itype in ("equity", "stock", "share"):
            asset_class = "Equity"
        else:
            asset_class = resolve_asset_bucket(sebi_sub or sebi_cat, holding.instrument_name)
    base = PortfolioHoldingResponse.model_validate(holding)
    return base.model_copy(update={"asset_class": asset_class, "sub_category": sebi_sub})


@router.get("/", response_model=PortfolioDetailResponse)
async def get_portfolio(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    # Re-mark the portfolio to today's NAV so the headline net worth is current
    # (units × latest NAV) rather than the frozen CAMS statement-date valuation.
    # Best-effort: if NAV is unavailable the holdings keep their stored value.
    try:
        await revalue_primary_portfolio_at_latest_nav(db, current_user.id)
    except Exception:  # noqa: BLE001 — never let re-valuation block the read
        await db.rollback()

    stmt = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.allocations),
            selectinload(Portfolio.holdings).selectinload(PortfolioHolding.fund_metadata),
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
        holdings=[_build_holding_response(h) for h in portfolio.holdings],
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
        select(PortfolioHolding)
        .options(selectinload(PortfolioHolding.fund_metadata))
        .where(PortfolioHolding.portfolio_id == portfolio.id)
    )
    return [_build_holding_response(h) for h in result.scalars().all()]


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
    horizon: str = Query(default="1Y", pattern="^(?i)(1M|3M|1Y|3Y|MAX)$"),
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
    """Recompute the user's daily net-worth series from stored NAV (no fetch).

    Fast path that assumes NAV history is already present (it does not reach out
    to mfapi.in). For the first build — when NAV may be missing back to the first
    transaction — use ``POST /portfolio/networth-history/build`` instead.
    """
    await compute_user_networth_history(db, current_user.id)
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


def _job_status(job, *, has_history: bool) -> NetworthJobStatusResponse:
    if job is None:
        return NetworthJobStatusResponse(
            status="none", progress_pct=0, has_history=has_history
        )
    return NetworthJobStatusResponse(
        status=job.status,
        phase=job.phase,
        progress_pct=float(job.progress_pct or 0),
        message=job.message,
        history_from=job.history_from,
        days_total=int(job.days_total) if job.days_total is not None else None,
        has_history=has_history,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _has_networth_history(db: AsyncSession, user_id) -> bool:
    rows = await get_user_nav_history(db, user_id, horizon="MAX")
    return len(rows) > 0


@router.get("/networth-history/status", response_model=NetworthJobStatusResponse)
async def networth_history_status(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Poll the one-time backfill job (status + % completion) for the dashboard CTA."""
    job = await get_latest_job(db, current_user.id)
    has_history = await _has_networth_history(db, current_user.id)
    return _job_status(job, has_history=has_history)


@router.post("/networth-history/build", response_model=NetworthJobStatusResponse)
async def build_networth_history(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Kick off the one-time net-worth-history backfill (NAV fetch + compute).

    Returns immediately; the client polls ``/networth-history/status`` for the %.
    Idempotent: if a build is already pending/running, the existing job is returned.
    """
    running = await has_running_job(db, current_user.id)
    if running is not None:
        return _job_status(running, has_history=await _has_networth_history(db, current_user.id))

    job = await create_job(db, current_user.id)
    background.add_task(run_networth_backfill, current_user.id, job.id)
    return _job_status(job, has_history=False)


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
