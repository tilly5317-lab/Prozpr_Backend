"""FastAPI router — `onboarding.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.user import User
from app.models.profile import OtherInvestment, OtherInvestmentStatus, PersonalFinanceProfile
from app.services.effective_risk_profile import maybe_recalculate_effective_risk
from app.schemas.onboarding import (
    OnboardingCompleteRequest,
    OnboardingProfileCreate,
    OnboardingProfileResponse,
    OtherAssetBulkCreate,
    OtherAssetResponse,
)

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


def _other_investment_to_legacy_response(row: OtherInvestment) -> OtherAssetResponse:
    return OtherAssetResponse(
        id=row.id,
        asset_name=row.investment_name,
        asset_type=row.investment_type,
        current_value=float(row.present_value),
    )


@router.post("/profile", response_model=OnboardingProfileResponse)
async def save_onboarding_profile(
    payload: OnboardingProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == current_user.id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing:
        payload_data = payload.model_dump(exclude_unset=True)
        date_of_birth = payload_data.pop("date_of_birth", None)
        for field, value in payload_data.items():
            setattr(existing, field, value)
        if date_of_birth is not None:
            user.date_of_birth = date_of_birth
        profile = existing
    else:
        payload_data = payload.model_dump()
        user.date_of_birth = payload_data.pop("date_of_birth", None)
        profile = PersonalFinanceProfile(user_id=current_user.id, **payload_data)
        db.add(profile)

    await db.commit()
    await db.refresh(profile)
    await maybe_recalculate_effective_risk(db, current_user.id, "onboarding_profile_update")
    await db.commit()

    return OnboardingProfileResponse(
        user_id=current_user.id,
        date_of_birth=user.date_of_birth,
        selected_goals=profile.selected_goals or [],
        custom_goals=profile.custom_goals or [],
        investment_horizon=profile.investment_horizon,
        annual_income_min=profile.annual_income_min,
        annual_income_max=profile.annual_income_max,
        annual_expense_min=profile.annual_expense_min,
        annual_expense_max=profile.annual_expense_max,
    )


@router.get("/profile", response_model=OnboardingProfileResponse)
async def get_onboarding_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not profile or not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    return OnboardingProfileResponse(
        user_id=current_user.id,
        date_of_birth=user.date_of_birth,
        selected_goals=profile.selected_goals or [],
        custom_goals=profile.custom_goals or [],
        investment_horizon=profile.investment_horizon,
        annual_income_min=profile.annual_income_min,
        annual_income_max=profile.annual_income_max,
        annual_expense_min=profile.annual_expense_min,
        annual_expense_max=profile.annual_expense_max,
    )


@router.post("/other-assets", response_model=list[OtherAssetResponse])
async def save_other_assets(
    payload: OtherAssetBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await db.execute(delete(OtherInvestment).where(OtherInvestment.user_id == current_user.id))

    today = datetime.now(timezone.utc).date()
    assets = []
    for item in payload.assets:
        pv = item.current_value if item.current_value is not None else 0.0
        inv = OtherInvestment(
            user_id=current_user.id,
            investment_type=(item.asset_type or "OTHER").strip()[:50],
            investment_name=item.asset_name.strip()[:200],
            present_value=pv,
            as_of_date=today,
            status=OtherInvestmentStatus.ACTIVE,
        )
        db.add(inv)
        assets.append(inv)

    await db.commit()
    for a in assets:
        await db.refresh(a)

    return [_other_investment_to_legacy_response(a) for a in assets]


@router.get("/other-assets", response_model=list[OtherAssetResponse])
async def get_other_assets(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(OtherInvestment).where(OtherInvestment.user_id == current_user.id)
    result = await db.execute(stmt)
    return [_other_investment_to_legacy_response(a) for a in result.scalars().all()]


@router.post("/complete")
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_onboarding_complete = payload.is_complete
    await db.commit()
    return {"message": "Onboarding completed", "is_onboarding_complete": payload.is_complete}
