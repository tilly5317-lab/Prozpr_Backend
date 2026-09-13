"""FastAPI router — additional-investment read surface.

Serves the customer's latest monthly-SIP deployment plan to the Invest page. The
plan itself is generated inside chat (the ``additional_investment`` intent
persists the run); this router only reads it back. BUY-only / write-once, so
there is no create/update route here — new plans are produced by chatting.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.core.progress import clear_progress, get_progress, set_progress
from app.domains.additional_investment.models.additional_investment_run import (
    AdditionalInvestmentRun,
)
from app.domains.additional_investment.schemas import (
    LumpsumCreateRequest,
    LumpsumPlanResponse,
    PreferenceActivationResponse,
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
    get_ainv_plan_for_run,
    get_latest_lumpsum_plan,
    get_latest_sip_plan,
    get_session_current_ainv,
)
from app.domains.identity.models.user import User

router = APIRouter(
    prefix="/additional-investment", tags=["Additional Investment"]
)

_SIP_PROGRESS_TASK = "sip_plan_compute"


class ComputeProgressResponse(BaseModel):
    """Live stage of an in-flight SIP build (polled while the POST runs).

    ``messages`` is the full stage history so far (oldest first) so the UI can
    show every completed step even when stages advance faster than the poll.
    """

    active: bool
    progress_pct: float
    message: str | None = None
    messages: list[str] = []


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


class AdditionalInvestmentCurrentResponse(BaseModel):
    """A chat session's latest additional-investment run, for restoring the chat
    pills on reload (history carries no per-message pill data). ``cadence``
    restores "View plan" for ANY deploy; ``save_preference_run_id`` restores the
    "Save preference" pill, and is set only when that latest run carries an
    unsaved what-if candidate. Both null when the session made no such run."""

    cadence: str | None = None
    save_preference_run_id: str | None = None


@router.get("/current", response_model=AdditionalInvestmentCurrentResponse)
async def get_current_ainv(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
) -> AdditionalInvestmentCurrentResponse:
    """Latest additional-investment run in the given chat session, for restoring
    the chat pills — the mirror of ``GET /rebalancing/current``, but
    session-scoped (AINV has two cadences). ``cadence`` ("sip_monthly" |
    "lumpsum") restores "View plan"; ``save_preference_run_id`` restores the
    "Save preference" pill when the latest run has an unsaved what-if candidate."""
    cadence, save_run_id = await get_session_current_ainv(
        db, current_user.id, session_id
    )
    return AdditionalInvestmentCurrentResponse(
        cadence=cadence,
        save_preference_run_id=str(save_run_id) if save_run_id else None,
    )


@router.get("/run/{run_id}", response_model=None)
async def get_run_plan(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
) -> SipPlanResponse | LumpsumPlanResponse:
    """One specific run's plan by id — SIP or lump-sum, discriminated by cadence.

    ORIGIN-AGNOSTIC on purpose: the chat "View plan" popup uses this to open an
    unsaved what-if (``origin='candidate'``) draft that the latest-plan reads
    (``GET /sip``, ``/lumpsum``) firewall out. 404 when the run is not the
    customer's. Distinct ``/run/`` prefix so it never shadows ``/sip`` / ``/lumpsum``
    / ``/current``.
    """
    plan = await get_ainv_plan_for_run(db, current_user.id, run_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Additional-investment run not found",
        )
    return plan


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


@router.post(
    "/{run_id}/save-preference", response_model=PreferenceActivationResponse
)
async def save_run_preference(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_ctx: User = Depends(get_ai_user_context),
) -> PreferenceActivationResponse:
    """Activate the candidate investment-preference row this run was computed under (Prozpr's AINV pill)."""
    from app.domains.profile.services.preference_save_service import (
        activate_candidate_for_run,
    )

    # user_ctx is loaded from the effective user (get_ai_user_context depends
    # on get_effective_user), so scoping the run lookup by user_ctx.id keeps
    # the lookup and the activation below on the SAME effective user -- an
    # advisor/impersonation split can never mis-scope one against the other.
    result = await db.execute(
        select(AdditionalInvestmentRun).where(
            AdditionalInvestmentRun.id == run_id,
            AdditionalInvestmentRun.user_id == user_ctx.id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Additional-investment run not found",
        )
    activated = await activate_candidate_for_run(
        db, user_ctx, run.saved_investment_preference_id
    )
    return PreferenceActivationResponse(activated=activated)
