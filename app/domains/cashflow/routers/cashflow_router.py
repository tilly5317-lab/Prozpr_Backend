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
from app.domains.cashflow.schemas.readiness import CashflowReadinessResponse
from app.domains.cashflow.services.cashflow_persist_service import (
    get_latest_plan_run,
    persist_plan_run,
)
from app.domains.cashflow.services.goal_planning_engine.readiness import (
    evaluate_cashflow_readiness,
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

    annual = [
        AnnualCashflowRowSchema.model_validate(r) for r in (run.annual_rows or [])
    ]
    monthly = (
        [MonthlyCashflowRowSchema.model_validate(r) for r in (run.monthly_rows or [])]
        if run.monthly_rows
        else None
    )

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


def _current_engine_version() -> str | None:
    """Current cashflow engine version, or None if it can't be resolved.

    Used to auto-invalidate plan runs computed by an older engine — so an engine
    upgrade (e.g. dropping the auto-injected retirement goal) takes effect on the
    next read without anyone having to mark every run stale by hand.
    """
    try:
        from app.domains.ai_engine.common import ensure_ai_agents_path

        ensure_ai_agents_path()
        from cashflow_statement.engine import ENGINE_VERSION

        return ENGINE_VERSION
    except Exception:  # pragma: no cover - defensive; never block a read on this
        logger.warning("could not resolve cashflow ENGINE_VERSION", exc_info=True)
        return None


def _raise_for_input_error(err: ValueError) -> None:
    """Translate input-builder gate errors into a 422 the frontend can act on."""
    msg = str(err)
    if msg == "missing_date_of_birth":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Date of birth is required for the cashflow projection. Please complete your profile.",
        )
    if msg.startswith("missing_required_inputs:"):
        keys = [k for k in msg.split(":", 1)[1].split(",") if k]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Complete the required cashflow inputs to run goal planning.",
                "missing": keys,
            },
        )
    raise err


async def _compute_and_persist(
    db: AsyncSession, user: User
) -> CashflowPlanRunDetailResponse:
    """Run the cashflow engine and persist the result."""
    from app.domains.cashflow.services.cashflow_compute_service import (
        run_cashflow_projection_for_user,
    )
    from app.domains.portfolio.services.portfolio_service import (
        get_primary_portfolio,
        revalue_primary_portfolio_at_latest_nav,
    )

    # The current portfolio value feeds the engine's starting corpus (single
    # source of truth — the portfolio/CAMS data), summed with cash & assets.
    # Re-mark it to *today's* NAV first (the same revaluation the /portfolio
    # dashboard uses) so the corpus tracks the current value, not the frozen
    # statement-date figure. Falls back to the stored row if there is nothing to
    # revalue (no holdings / no primary portfolio).
    portfolio = await revalue_primary_portfolio_at_latest_nav(db, user.id)
    if portfolio is None:
        portfolio = await get_primary_portfolio(db, user.id)
    portfolio_value = (
        float(portfolio.total_value)
        if portfolio is not None and portfolio.total_value is not None
        else None
    )

    snapshot = await run_cashflow_projection_for_user(
        user,
        anchor_date=date.today(),
        detail_level="full",
        portfolio_value=portfolio_value,
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
    """Return the latest cashflow plan run. Auto-computes if stale, missing, or
    produced by an older engine version (so engine upgrades take effect on read)."""
    existing = await get_latest_plan_run(db, current_user.id)

    current_version = _current_engine_version()
    version_stale = (
        existing is not None
        and current_version is not None
        and existing.engine_version != current_version
    )

    if (
        existing is None
        or existing.is_stale
        or existing.assumption_id is None
        or version_stale
    ):
        try:
            return await _compute_and_persist(db, user)
        except ValueError as e:
            _raise_for_input_error(e)

    # The plan is usable. Refresh it at most once per calendar day so the
    # starting corpus tracks the portfolio's current (today's-NAV) value — but
    # never fail the read on this: if a daily refresh can't run (e.g. a required
    # input was cleared since), keep serving the last good plan.
    day_stale = (
        existing is not None
        and existing.computed_at is not None
        and existing.computed_at.date() < date.today()
    )
    if day_stale:
        try:
            return await _compute_and_persist(db, user)
        except ValueError:
            logger.info(
                "daily cashflow refresh skipped (incomplete inputs) — serving last plan"
            )
        except Exception:  # noqa: BLE001 — a refresh must never break the read
            logger.warning(
                "daily cashflow refresh failed — serving last plan", exc_info=True
            )

    return _serialize_plan_run(existing)


@router.get("/readiness", response_model=CashflowReadinessResponse)
async def get_cashflow_readiness(
    current_user: CurrentUser = Depends(get_effective_user),
    user: User = Depends(get_ai_user_context),
):
    """Report which cashflow inputs are present vs. still required from the user."""
    return CashflowReadinessResponse(**evaluate_cashflow_readiness(user))


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
        _raise_for_input_error(e)
