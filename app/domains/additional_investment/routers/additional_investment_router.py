"""FastAPI router — additional-investment read surface.

Serves the customer's latest monthly-SIP deployment plan to the Invest page. The
plan itself is generated inside chat (the ``additional_investment`` intent
persists the run); this router only reads it back. BUY-only / write-once, so
there is no create/update route here — new plans are produced by chatting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.domains.additional_investment.schemas import SipCreateRequest, SipPlanResponse
from app.domains.additional_investment.services.additional_investment_create_service import (
    create_sip_plan_for_user,
)
from app.domains.additional_investment.services.additional_investment_read_service import (
    get_latest_sip_plan,
)
from app.domains.identity.models.user import User

router = APIRouter(
    prefix="/additional-investment", tags=["Additional Investment"]
)


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
    return await create_sip_plan_for_user(
        db,
        user,
        acting_user_id=user.id,
        monthly_amount_inr=payload.monthly_amount_inr,
    )
