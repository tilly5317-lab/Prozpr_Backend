"""FastAPI router — rebalancing run listing, detail, and status update.

Backed by the normalized ``rebalancing_*`` family. List endpoint returns light
rows; detail endpoint eager-loads totals, subgroup summaries, trades, and
warnings so the UI gets one round-trip per run.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.identity.models.user import User
from app.domains.mutual_funds.models.mf_transaction import MfTransaction
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding
from app.domains.portfolio.services.allocation_rollup import current_asset_class_mix
from app.domains.rebalancing.models.rebalancing_run import (
    RebalancingRun,
    RebalancingRunStatus,
)
from app.domains.rebalancing.schemas import (
    AssetClassBreakdownRow,
    RebalancingAssetClassBreakdown,
    RebalancingReadinessField,
    RebalancingReadinessResponse,
    RebalancingRunDetailResponse,
    RebalancingRunListItem,
    RebalancingStatusUpdate,
)
from app.domains.rebalancing.services.asset_class_breakdown import (
    current_mix_from_rows,
    plan_rows_from_run,
    run_current_asset_class_mix,
    target_asset_class_mix,
    target_mix_from_rows,
)
from app.domains.rebalancing.services.saved_plan_service import (
    save_plan,
    select_current_run_id,
)

router = APIRouter(prefix="/rebalancing", tags=["Rebalancing"])

# Shared eager-load set for the full run detail (get_run + get_current). Load-
# bearing: _build_asset_class_breakdown reads fund_rows for the per-fund
# look-through — do not prune, or the Current-vs-Target bars silently break.
_DETAIL_LOADS = (
    selectinload(RebalancingRun.totals),
    selectinload(RebalancingRun.subgroup_summaries),
    selectinload(RebalancingRun.trades),
    selectinload(RebalancingRun.fund_rows),
    selectinload(RebalancingRun.warnings),
    selectinload(RebalancingRun.portfolio)
    .selectinload(Portfolio.holdings)
    .selectinload(PortfolioHolding.fund_metadata),
)


@router.get("/", response_model=list[RebalancingRunListItem])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRun)
        .where(RebalancingRun.user_id == current_user.id)
        .order_by(RebalancingRun.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [RebalancingRunListItem.model_validate(r) for r in rows]


@router.get("/readiness", response_model=RebalancingReadinessResponse)
async def get_readiness(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Inputs the rebalancing engine requires before it can run.

    Mirrors the cashflow readiness gate. The engine hard-blocks on two things
    (see ``compute_rebalancing_result``): the user's **date of birth** (anchors
    tax aging + risk) and the presence of **mutual-fund holdings**. The UI uses
    this to show an unlock form / connect-portfolio CTA instead of firing the
    compute call and getting a blocking message back.

    NOTE: declared *before* ``/{run_id}`` so the literal path isn't parsed as a
    run UUID (which previously made this 422).
    """
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    dob = getattr(user, "date_of_birth", None)
    dob_present = dob is not None

    has_holdings = (
        await db.execute(
            select(MfTransaction.id)
            .where(MfTransaction.user_id == current_user.id)
            .limit(1)
        )
    ).first() is not None

    fields = [
        RebalancingReadinessField(
            key="date_of_birth",
            label="Date of birth",
            group="About you",
            kind="date",
            help="Anchors your tax aging (LTCG/STCG) and risk profile.",
            optional=False,
            present=dob_present,
            value=dob.isoformat() if dob_present else None,
        )
    ]
    missing = [f.key for f in fields if not f.optional and not f.present]
    ready = not missing and has_holdings

    return RebalancingReadinessResponse(
        ready=ready,
        missing=missing,
        fields=fields,
        has_holdings=has_holdings,
    )


@router.get("/current", response_model=RebalancingRunDetailResponse)
async def get_current(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The customer's committed plan (``origin='saved'``) if any, else the
    latest run by ``created_at``.

    Declared BEFORE ``/{run_id}`` so the literal ``current`` isn't parsed as a
    run UUID (mirrors ``/readiness``).
    """
    run_id = await select_current_run_id(db, user_id=current_user.id)
    if run_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No rebalancing plan yet"
        )
    run = (
        await db.execute(
            select(RebalancingRun)
            .where(
                RebalancingRun.id == run_id,
                RebalancingRun.user_id == current_user.id,
            )
            .options(*_DETAIL_LOADS)
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    resp = RebalancingRunDetailResponse.model_validate(run)
    resp.asset_class_breakdown = _build_asset_class_breakdown(run)
    return resp


@router.get("/{run_id}", response_model=RebalancingRunDetailResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRun)
        .where(
            RebalancingRun.id == run_id,
            RebalancingRun.user_id == current_user.id,
        )
        .options(*_DETAIL_LOADS)
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    resp = RebalancingRunDetailResponse.model_validate(run)
    resp.asset_class_breakdown = _build_asset_class_breakdown(run)
    return resp


# Canonical bar order for the Current-vs-Target view.
_BREAKDOWN_ORDER: tuple[str, ...] = ("Equity", "Debt", "Others")


def _build_asset_class_breakdown(run: RebalancingRun) -> RebalancingAssetClassBreakdown:
    """Hybrid-aware Equity/Debt/Others split for the Invest-page bars.

    Built from the run's own per-fund rows + trades via the SHARED rollup in
    ``asset_class_breakdown`` — the same call the rebalancing chat facts pack
    makes, so the page and chat cannot quote different splits for one run. Both
    bars share the engine's valuation basis, so their totals differ only by the
    plan's net cash flow (≈0).

    Fallbacks, in order: per-subgroup totals when a legacy run has no fund rows
    (no sub_category, so no look-through), then the portfolio-holdings rollup
    when it has no subgroup summaries either — the latter's statement-NAV total
    can sit a few percent off the engine's today's-NAV total, which rendered the
    Current bar shorter than the Target bar.
    """
    fund_rows = list(run.fund_rows or [])
    subs = list(run.subgroup_summaries or [])
    if fund_rows:
        current_rows, target_rows = plan_rows_from_run(fund_rows, list(run.trades or []))
        current_mix = current_mix_from_rows(current_rows)
        target_mix = target_mix_from_rows(target_rows)
    else:
        target_mix = target_asset_class_mix(subs)
        if subs:
            current_mix = run_current_asset_class_mix(subs)
        else:
            holdings = list(run.portfolio.holdings) if run.portfolio else []
            current_mix = current_asset_class_mix(holdings)

    rows = [
        AssetClassBreakdownRow(
            asset_class=asset_class,
            current_inr=round(current_mix.get(asset_class, 0.0), 2),
            target_inr=round(target_mix.get(asset_class, 0.0), 2),
        )
        for asset_class in _BREAKDOWN_ORDER
        if current_mix.get(asset_class, 0.0) > 0 or target_mix.get(asset_class, 0.0) > 0
    ]
    return RebalancingAssetClassBreakdown(
        rows=rows,
        current_total_inr=round(sum(current_mix.values()), 2),
        target_total_inr=round(sum(target_mix.values()), 2),
    )


@router.put("/{run_id}/status", response_model=RebalancingRunListItem)
async def update_status(
    run_id: uuid.UUID,
    payload: RebalancingStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(RebalancingRun).where(
        RebalancingRun.id == run_id,
        RebalancingRun.user_id == current_user.id,
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )

    run.status = RebalancingRunStatus(payload.status)
    await db.commit()
    await db.refresh(run)
    return RebalancingRunListItem.model_validate(run)


@router.post("/{run_id}/save", response_model=RebalancingRunListItem)
async def save_run_as_plan(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Mark a run as the customer's committed plan (idempotent). Demotes any
    prior saved run so exactly one stays committed. Owns its commit, mirroring
    ``update_status``."""
    run = await save_plan(db, user_id=current_user.id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    await db.commit()
    await db.refresh(run)
    return RebalancingRunListItem.model_validate(run)
