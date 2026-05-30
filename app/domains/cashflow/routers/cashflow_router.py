"""FastAPI router — cashflow plan runs.

Provides endpoints for retrieving and computing cashflow projections.
Auto-computes when no run exists or the latest run is marked stale.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.domains.identity.models.user import User
from app.domains.cashflow.schemas.outputs import (
    AnnualCashflowRowSchema,
    CashflowPlanRunDetailResponse,
    FundFlowSummarySchema,
    HeadlineStatusSchema,
    MonthlyCashflowRowSchema,
    PlanSummarySchema,
)
from app.domains.cashflow.services.cashflow_persist_service import (
    get_latest_plan_run,
    persist_plan_run,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cashflow", tags=["Cashflow"])


def _serialize_plan_run(run) -> CashflowPlanRunDetailResponse:
    """Convert ORM plan run with children to response schema."""
    headline = None
    if run.headline:
        headline = HeadlineStatusSchema.model_validate(run.headline)

    fund_flow = None
    if run.fund_flow_summary:
        fund_flow = FundFlowSummarySchema.model_validate(run.fund_flow_summary)

    plan_summary = None
    if run.plan_summary:
        plan_summary = PlanSummarySchema(
            top_line=run.plan_summary.top_line,
            retirement_note=run.plan_summary.retirement_note,
            cashflow_note=run.plan_summary.cashflow_note,
            goals=run.plan_summary.goals,
            risks=run.plan_summary.risks,
            next_steps=run.plan_summary.next_steps,
            summary_error=run.plan_summary.summary_error,
        )

    annual = [AnnualCashflowRowSchema.model_validate(r) for r in (run.annual_rows or [])]
    monthly = [MonthlyCashflowRowSchema.model_validate(r) for r in (run.monthly_rows or [])] if run.monthly_rows else None

    return CashflowPlanRunDetailResponse(
        id=run.id,
        user_id=run.user_id,
        chat_session_id=run.chat_session_id,
        engine_version=run.engine_version,
        cause=run.cause,
        assumption_id=run.assumption_id or run.id,
        warnings=run.warnings or [],
        computed_at=run.computed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        headline=headline,
        fund_flow_summary=fund_flow,
        plan_summary=plan_summary,
        annual_cashflow=annual,
        monthly_cashflow=monthly,
    )


async def _compute_and_persist(db: AsyncSession, user: User) -> CashflowPlanRunDetailResponse:
    """Run the cashflow engine and persist the result."""
    from app.domains.cashflow.services.cashflow_compute_service import run_cashflow_projection_for_user

    snapshot = await run_cashflow_projection_for_user(
        user, anchor_date=date.today(), detail_level="full"
    )
    await persist_plan_run(db, user.id, snapshot)

    latest = await get_latest_plan_run(db, user.id)
    if not latest:
        raise HTTPException(status_code=500, detail="Failed to persist plan run")
    return _serialize_plan_run(latest)


@router.get("/latest", response_model=CashflowPlanRunDetailResponse)
async def get_latest_cashflow(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user: User = Depends(get_ai_user_context),
):
    """Return the latest cashflow plan run. Auto-computes if stale or missing."""
    existing = await get_latest_plan_run(db, current_user.id)

    if existing is None or existing.is_stale or existing.assumption_id is None:
        try:
            return await _compute_and_persist(db, user)
        except ValueError as e:
            if str(e) == "missing_date_of_birth":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Date of birth is required for cashflow projection. Please complete your profile.",
                )
            raise

    return _serialize_plan_run(existing)


@router.post("/compute", response_model=CashflowPlanRunDetailResponse)
async def compute_cashflow(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user: User = Depends(get_ai_user_context),
):
    """Force-recompute cashflow projection and persist."""
    try:
        return await _compute_and_persist(db, user)
    except ValueError as e:
        if str(e) == "missing_date_of_birth":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Date of birth is required for cashflow projection. Please complete your profile.",
            )
        raise
