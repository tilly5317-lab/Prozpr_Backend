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
    run_current_asset_class_mix,
    target_asset_class_mix,
)

router = APIRouter(prefix="/rebalancing", tags=["Rebalancing"])


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
        .options(
            selectinload(RebalancingRun.totals),
            selectinload(RebalancingRun.subgroup_summaries),
            selectinload(RebalancingRun.trades),
            selectinload(RebalancingRun.warnings),
            selectinload(RebalancingRun.portfolio)
            .selectinload(Portfolio.holdings)
            .selectinload(PortfolioHolding.fund_metadata),
        )
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
    """Multi-asset-aware Equity/Debt/Others split for the Invest-page bars.

    BOTH bars come from the plan's per-subgroup totals (``current_holding_inr`` /
    ``suggested_final_holding_inr``) so they share one valuation basis and their
    totals differ only by the plan's net cash flow (≈0). The engine's generic
    ``multi_asset`` sleeve is split by the 65/25/10 composition the engine sized
    it as — matching the engine ideal shown in chat. The portfolio-holdings
    rollup is only a fallback for legacy runs without subgroup summaries — its
    statement-NAV total can sit a few percent off the engine's today's-NAV
    total, which rendered the Current bar shorter than the Target bar.
    """
    subs = list(run.subgroup_summaries or [])
    if subs:
        current_mix = run_current_asset_class_mix(subs)
    else:
        holdings = list(run.portfolio.holdings) if run.portfolio else []
        current_mix = current_asset_class_mix(holdings)
    target_mix = target_asset_class_mix(run.subgroup_summaries)

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
