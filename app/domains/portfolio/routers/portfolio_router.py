"""FastAPI router — `portfolio.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cas_scope import effective_scope, scope_filter
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.asset_allocation.models.run import AssetAllocationRun
from app.domains.mutual_funds.models.enums import PortfolioSnapshotKind
from app.domains.mutual_funds.models.mf_allocation_snapshot import (
    PortfolioAllocationSnapshot,
)
from app.domains.portfolio.models.portfolio import (
    Portfolio,
    PortfolioAllocation,
    PortfolioHistory,
    PortfolioHolding,
)
from app.domains.ingestion.schemas.finvu import (
    FinvuPortfolioSyncRequest,
    FinvuPortfolioSyncResponse,
)
from app.domains.portfolio.services import networth
from app.domains.portfolio.services.networth import reader as networth_reader
from app.domains.portfolio.services.twr_service import compute_twr_series
from app.domains.portfolio.services.allocation_rollup import (
    current_asset_class_mix,
    current_sub_category_mix,
    holding_single_asset_class,
)
from app.domains.portfolio.schemas.portfolio import (
    AllocationSubCategoryResponse,
    PortfolioAllocationBulkUpdate,
    PortfolioAllocationResponse,
    PortfolioDetailResponse,
    PortfolioHistoryResponse,
    PortfolioNavHistoryPoint,
    PortfolioNavHistoryResponse,
    NetworthJobStatusResponse,
    TwrSeriesResponse,
    PortfolioHoldingResponse,
    PortfolioResponse,
    RecommendedPlanResponse,
    RecommendedPlanSnapshotResponse,
)
from app.domains.ingestion.services.finvu_portfolio_sync import (
    apply_finvu_bucket_snapshot,
)
from app.domains.profile.services._effective_risk import (
    maybe_recalculate_effective_risk,
)
from app.domains.portfolio.services.portfolio_service import (
    get_or_create_primary_portfolio,
)

# A manual rebuild inside this window, with no new statement behind it, is
# answered with the existing job instead of queueing another full recompute.
_MIN_REBUILD_INTERVAL = timedelta(seconds=60)

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
    """Serialize a holding with the canonical asset_class + SEBI sub_category.

    The holdings list shows each fund's dominant single asset_class (no
    look-through split); the donut's split breakdown is derived separately in
    ``_derive_allocations``. They share ``allocation_rollup``'s CLASSIFIER but
    not its split, so for any portfolio holding a blended fund the two DISAGREE
    by design: summing `holdings` by `asset_class` over-counts the dominant
    class. Anything needing the split view (the donut, its drill-down) must read
    `allocations[]` / `allocations[].sub_categories`, never re-derive from here.
    """
    md = holding.fund_metadata
    sebi_sub = md.sub_category if md else None
    asset_class = holding_single_asset_class(holding)
    base = PortfolioHoldingResponse.model_validate(holding)
    return base.model_copy(
        update={"asset_class": asset_class, "sub_category": sebi_sub}
    )


# Canonical legend order for the current-allocation donut.
_ALLOC_ORDER: dict[str, int] = {"Equity": 0, "Debt": 1, "Others": 2, "Cash": 3}


def _derive_allocations(
    portfolio_id: uuid.UUID,
    holdings: list[PortfolioHolding],
    persisted: list[PortfolioAllocation],
) -> list[PortfolioAllocationResponse]:
    """Derive the current-allocation breakdown LIVE from holdings.

    The donut and the holdings list must agree, so both come from one place:
    each holding's canonical ``_holding_asset_class`` summed at today's
    ``current_value``. This replaces the stale, ingest-time
    ``portfolio_allocations`` rows whose mix was frozen at statement date and
    classified by scheme name only (no SEBI sub_category yet).

    Non-holding assets — i.e. a SimBanks bank ``Cash`` balance — have no holding
    to sum, so any persisted ``Cash`` row is carried forward as-is.
    """
    # Blended funds (multi-asset / hybrid) are split across Equity/Debt/Others via
    # the central look-through; everything else lands in its single class. Shared
    # with chat's current-mix so the two can never disagree.
    amounts: dict[str, float] = current_asset_class_mix(holdings)
    # Per-class sub-category breakdown for the slice drill-down, split by the same
    # look-through so each class's rows sum to that class's own amount.
    sub_mix: dict[str, dict[str, float]] = current_sub_category_mix(holdings)
    # Carry forward bank cash — the only persisted bucket with no holding behind it.
    for a in persisted:
        if (a.asset_class or "").strip().lower() == "cash":
            amounts["Cash"] = amounts.get("Cash", 0.0) + float(a.amount or 0)

    total = sum(amounts.values())
    if total <= 0:
        return []
    rows = [
        PortfolioAllocationResponse(
            id=uuid.uuid5(uuid.NAMESPACE_OID, f"alloc:{portfolio_id}:{ac}"),
            asset_class=ac,
            allocation_percentage=round(100.0 * amt / total, 2),
            amount=round(amt, 2),
            performance_percentage=None,
            sub_categories=[
                AllocationSubCategoryResponse(name=name, amount=round(sub_amt, 2))
                for name, sub_amt in sorted(
                    sub_mix.get(ac, {}).items(), key=lambda kv: kv[1], reverse=True
                )
                if sub_amt > 0
            ],
        )
        for ac, amt in amounts.items()
        if amt > 0
    ]
    rows.sort(key=lambda r: _ALLOC_ORDER.get(r.asset_class, 99))
    return rows


@router.get("/", response_model=PortfolioDetailResponse)
async def get_portfolio(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.allocations),
            selectinload(Portfolio.holdings).selectinload(
                PortfolioHolding.fund_metadata
            ),
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

    # Recalculate portfolio.total_value and total_invested from current holdings
    # to ensure they match the holdings list when multiple CAS snapshots exist.
    # The ORM hook filters holdings to only the active snapshot, but these values
    # might be stale from an earlier ingest that included archived holdings.
    holdings = list(portfolio.holdings)
    if holdings:
        current_total_value = sum(float(h.current_value or 0) for h in holdings)
        current_total_invested = sum(
            float(h.average_cost or 0) * float(h.quantity or 0)
            for h in holdings
            if h.average_cost is not None and h.quantity is not None and float(h.quantity or 0) > 0
        )
        # Only update if we have valid calculated values; preserve originals as fallback
        if current_total_value > 0:
            portfolio.total_value = current_total_value
        if current_total_invested > 0:
            portfolio.total_invested = current_total_invested
            portfolio.total_gain_percentage = round(
                (current_total_value - current_total_invested) / current_total_invested * 100, 2
            )
        elif current_total_value > 0:
            # Holdings exist but no cost basis; calculate from value alone
            portfolio.total_gain_percentage = None

    return PortfolioDetailResponse(
        **PortfolioResponse.model_validate(portfolio).model_dump(),
        allocations=_derive_allocations(
            portfolio.id, holdings, list(portfolio.allocations)
        ),
        holdings=[_build_holding_response(h) for h in holdings],
    )


@router.put("/allocations", response_model=list[PortfolioAllocationResponse])
async def update_allocations(
    payload: PortfolioAllocationBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)

    # Replaces the allocation rows of the CURRENT snapshot only; earlier
    # statements keep theirs. The replacements are stamped automatically on flush.
    snapshot_id = await effective_scope(db, current_user.id)
    await db.execute(
        delete(PortfolioAllocation).where(
            PortfolioAllocation.portfolio_id == portfolio.id,
            *scope_filter(PortfolioAllocation, snapshot_id),
        )
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
    await maybe_recalculate_effective_risk(
        db, current_user.id, "portfolio_allocation_update"
    )
    await db.commit()
    return [PortfolioAllocationResponse.model_validate(a) for a in allocations]


@router.get("/allocations", response_model=list[PortfolioAllocationResponse])
async def get_allocations(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    portfolio = await get_or_create_primary_portfolio(db, current_user.id)
    result = await db.execute(
        select(PortfolioAllocation).where(
            PortfolioAllocation.portfolio_id == portfolio.id
        )
    )
    return [
        PortfolioAllocationResponse.model_validate(a) for a in result.scalars().all()
    ]


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


# ─────────────────────── Net-worth history (daily series) ───────────────────────


def _nav_response(
    horizon: str, points: list, state, *, downsampled: bool
) -> PortfolioNavHistoryResponse:
    last = points[-1] if points else None
    return PortfolioNavHistoryResponse(
        horizon=horizon,
        points=[PortfolioNavHistoryPoint.model_validate(p) for p in points],
        total_invested=float(last.total_invested) if last else 0.0,
        current_value=float(last.total_value) if last else 0.0,
        gain_percentage=float(last.gain_percentage) if last else 0.0,
        as_of=state.last_date if state else None,
        is_stale=networth_reader.is_stale(state),
        degraded_schemes=int(state.degraded_schemes) if state else 0,
        ledger_complete=bool(state.ledger_complete) if state else True,
        downsampled=downsampled,
    )


@router.get("/nav-history", response_model=PortfolioNavHistoryResponse)
async def get_nav_history(
    response: Response,
    horizon: str = Query(default="1Y", pattern="^(?i)(1M|3M|1Y|3Y|MAX)$"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Daily per-user net-worth series for the dashboard chart.

    **Read-only.** This used to self-heal the trailing edge on every load — a write on
    the hottest read path in the product. Keeping the series current is the daily job's
    responsibility now (``services/networth/daily.py``); this reports staleness through
    ``is_stale`` instead of fixing it inline.

    Long horizons come back downsampled to a few hundred points. Every point is still a
    real stored day — nothing is averaged or interpolated.
    """
    horizon_norm = horizon.upper()
    state = await networth_reader.get_series_state(db, current_user.id)
    points = await networth_reader.get_user_nav_history(
        db, current_user.id, horizon=horizon_norm
    )

    if state is not None and state.built_at is not None:
        # Cheap revalidation for a series that only changes on a rebuild or the daily
        # pass — the dashboard refetches this on every horizon toggle.
        response.headers["ETag"] = (
            f'W/"{state.built_at.timestamp():.0f}-{state.last_date}-{horizon_norm}"'
        )
        response.headers["Cache-Control"] = "private, max-age=60"

    stored_days = int(state.row_count) if state else 0
    return _nav_response(
        horizon_norm,
        points,
        state,
        downsampled=bool(points) and len(points) < stored_days,
    )


@router.get("/twr", response_model=TwrSeriesResponse)
async def get_twr(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Real time-weighted return series — portfolio vs Nifty 50, mutual funds only.

    Returns the full daily growth-of-1 series since inception; the frontend rebases per
    selected range. ``has_data`` is false when there are fewer than 2 valued days.
    """
    return await compute_twr_series(db, current_user.id)


def _job_status(job, *, state) -> NetworthJobStatusResponse:
    has_history = bool(state and state.row_count)
    if job is None:
        return NetworthJobStatusResponse(
            status="none", progress_pct=0, has_history=has_history
        )
    return NetworthJobStatusResponse(
        status=job.status,
        phase=job.phase,
        progress_pct=float(job.progress_pct or 0),
        message=job.message,
        history_from=job.history_from or (state.first_date if state else None),
        days_total=int(job.days_total) if job.days_total is not None else None,
        has_history=has_history,
        started_at=job.started_at,
        finished_at=job.finished_at,
        trigger=job.trigger,
        warnings=job.warnings,
    )


@router.get("/networth-history/status", response_model=NetworthJobStatusResponse)
async def networth_history_status(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Poll the rebuild job (status + % completion) for the dashboard chart."""
    # Close out a build whose worker died mid-flight before reporting on it: the poller
    # is the one caller guaranteed to run while a user is actually looking at the
    # chart, so this is where an abandoned job becomes a "Try again" rather than a
    # progress bar that never moves.
    #
    # Guarded by a READ first. This endpoint is polled roughly every 1.8 seconds per
    # open dashboard, and running the reaping UPDATE unconditionally turns a read into
    # a fleet-wide write storm — it matches nothing on the overwhelmingly common
    # healthy path.
    try:
        if await networth.has_reapable_job(db, current_user.id):
            await networth.reap_stale_jobs(db, current_user.id)
    except Exception:  # noqa: BLE001 — never fail the poll on the repair
        await db.rollback()

    job = await networth.get_latest_job(db, current_user.id)
    state = await networth_reader.get_series_state(db, current_user.id)
    return _job_status(job, state=state)


@router.post("/networth-history/build", response_model=NetworthJobStatusResponse)
async def build_networth_history(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Rebuild the user's net-worth history from the transaction ledger.

    Returns immediately; the client polls ``/networth-history/status`` for the %.
    Idempotent: when a build is already in flight the existing job is returned rather
    than a second worker queued.
    """
    state = await networth_reader.get_series_state(db, current_user.id)

    # Rate-limit. The chart auto-POSTs this the first time it sees an empty series, so
    # it is reachable at page-load rate — but a rebuild the user's data cannot have
    # changed is pure load. A build is always allowed when the active statement differs
    # from the one the stored series was built from.
    if state is not None and state.built_at is not None:
        active_snapshot = await effective_scope(db, current_user.id)
        data_changed = state.built_from_cas_upload_id != active_snapshot
        age = datetime.now(timezone.utc) - state.built_at
        if not data_changed and age < _MIN_REBUILD_INTERVAL:
            job = await networth.get_latest_job(db, current_user.id)
            return _job_status(job, state=state)

    # ``create_job`` is the single-flight point: it reaps abandoned jobs, then either
    # creates ours or hands back the live one — the DB decides, via a partial unique
    # index. A check-then-create here instead is the race that once let one account
    # start three builds at once.
    job, created = await networth.create_job(
        db, current_user.id, trigger="manual", supersede=False
    )
    if not created:
        # We joined a build already in flight; queueing a second worker for it is
        # exactly the duplication the single-flight guard exists to prevent.
        return _job_status(job, state=state)

    background.add_task(
        networth.rebuild_user_networth, current_user.id, job.id, trigger="manual"
    )
    return _job_status(job, state=state)
