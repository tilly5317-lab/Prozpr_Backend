"""FastAPI router — additional-investment read surface.

Serves the customer's latest monthly-SIP deployment plan to the Invest page. The
plan itself is generated inside chat (the ``additional_investment`` intent
persists the run); this router only reads it back. BUY-only / write-once, so
there is no create/update route here — new plans are produced by chatting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.core.progress import clear_progress, get_progress, set_progress
from app.domains.additional_investment.schemas import (
    LumpsumCreateRequest,
    LumpsumPlanResponse,
    SipCreateRequest,
    SipPlanResponse,
)
from app.domains.additional_investment.services.additional_investment_create_service import (
    create_sip_plan_for_user,
)
from app.domains.additional_investment.services.additional_investment_lumpsum_create_service import (
    create_lumpsum_plan_for_user,
)
from app.domains.additional_investment.services.additional_investment_read_service import (
    get_latest_lumpsum_plan,
    get_latest_sip_plan,
)
from app.domains.identity.models.user import User

router = APIRouter(
    prefix="/additional-investment", tags=["Additional Investment"]
)

_SIP_PROGRESS_TASK = "sip_plan_compute"


class ComputeProgressResponse(BaseModel):
    """Live stage of an in-flight SIP build (polled while the POST runs)."""

    active: bool
    progress_pct: float
    message: str | None = None


@router.get("/sip", response_model=SipPlanResponse)
async def get_sip_plan(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
) -> SipPlanResponse:
    """Latest monthly-SIP deployment plan for the current user.

    Returns ``has_plan=False`` when the customer has not set up a SIP yet — the
    Invest page then shows its "start a SIP" prompt rather than a plan.
    """
    return await get_latest_sip_plan(db, current_user.id)


@router.post("/sip", response_model=SipPlanResponse)
async def create_sip_plan(
    payload: SipCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_ai_user_context),
) -> SipPlanResponse:
    """Set up a monthly SIP from the Invest page and return the fresh plan.

    Runs the same additional-investment engine chat uses (``cadence=sip_monthly``)
    for the given per-month amount, persists the run, and returns it in the read
    shape. ``get_ai_user_context`` already resolves the effective (family-member)
    user, so ``user.id`` is the acting user. 422 carries a customer-facing gate
    message when the profile is too incomplete to plan.
    """
    # Publish each pipeline stage to the in-process progress store so the
    # Invest page's poller (GET /sip/progress) can show real stage + %.
    async def _progress(pct: float, message: str) -> None:
        set_progress(user.id, _SIP_PROGRESS_TASK, pct, message)

    try:
        return await create_sip_plan_for_user(
            db,
            user,
            acting_user_id=user.id,
            monthly_amount_inr=payload.monthly_amount_inr,
            progress=_progress,
        )
    finally:
        clear_progress(user.id, _SIP_PROGRESS_TASK)


@router.get("/sip/progress", response_model=ComputeProgressResponse)
async def sip_build_progress(
    current_user: CurrentUser = Depends(get_effective_user),
) -> ComputeProgressResponse:
    """Live progress of this user's in-flight SIP build (if any)."""
    return ComputeProgressResponse(
        **get_progress(current_user.id, _SIP_PROGRESS_TASK)
    )


@router.get("/lumpsum", response_model=LumpsumPlanResponse)
async def get_lumpsum_plan(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
) -> LumpsumPlanResponse:
    """Latest one-time lump-sum deployment plan for the current user.

    Returns ``has_plan=False`` when the customer has not planned a lump sum yet —
    the Invest → Lump sum page then shows its set-up prompt. Carries the per-fund
    reasoning and the current-vs-ideal alignment behind the plan.
    """
    return await get_latest_lumpsum_plan(db, current_user.id)


@router.post("/lumpsum", response_model=LumpsumPlanResponse)
async def create_lumpsum_plan(
    payload: LumpsumCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_ai_user_context),
) -> LumpsumPlanResponse:
    """Plan a one-time lump sum from the Invest page and return the fresh plan.

    Runs the additional-investment engine (``cadence=lumpsum``) for the given
    one-time amount, which fills the customer's largest goal-based gaps, persists
    the run, and returns it in the read shape (with reasoning). ``action='add'``
    only — ``withdraw`` is rejected 422 (the engine is BUY-only). 422 also carries
    a customer-facing gate message when the profile is too incomplete to plan.
    """
    return await create_lumpsum_plan_for_user(
        db,
        user,
        acting_user_id=user.id,
        amount_inr=payload.amount_inr,
        action=payload.action,
    )
