"""FastAPI router — `onboarding.py`."""

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

_USER_FIELDS = {"date_of_birth", "occupation", "assumed_lifespan_years", "family_status", "address", "currency"}
_PFP_FIELDS = {
    "selected_goals",
    "custom_goals",
    "investment_horizon",
    "wealth_sources",
    "personal_values",
    "annual_income",
    "effective_tax_rate",
    "financial_assets",
    "financial_liabilities_excl_mortgage",
    "monthly_household_expense",
    "starting_monthly_investment",
}


def _other_investment_to_legacy_response(row: OtherInvestment) -> OtherAssetResponse:
    return OtherAssetResponse(
        id=row.id,
        asset_name=row.investment_name,
        asset_type=row.investment_type,
        current_value=float(row.present_value),
    )


def _profile_to_response(user: User, profile: PersonalFinanceProfile) -> OnboardingProfileResponse:
    return OnboardingProfileResponse(
        user_id=user.id,
        date_of_birth=user.date_of_birth,
        assumed_lifespan_years=user.assumed_lifespan_years,
        occupation=user.occupation,
        family_status=user.family_status,
        address=user.address,
        currency=user.currency,
        selected_goals=profile.selected_goals or [],
        custom_goals=profile.custom_goals or [],
        investment_horizon=profile.investment_horizon,
        wealth_sources=profile.wealth_sources or [],
        personal_values=profile.personal_values or [],
        annual_income=float(profile.annual_income) if profile.annual_income is not None else None,
        effective_tax_rate=(
            float(profile.effective_tax_rate) if profile.effective_tax_rate is not None else None
        ),
        financial_assets=(
            float(profile.financial_assets) if profile.financial_assets is not None else None
        ),
        financial_liabilities_excl_mortgage=(
            float(profile.financial_liabilities_excl_mortgage)
            if profile.financial_liabilities_excl_mortgage is not None
            else None
        ),
        monthly_household_expense=(
            float(profile.monthly_household_expense)
            if profile.monthly_household_expense is not None
            else None
        ),
        starting_monthly_investment=(
            float(profile.starting_monthly_investment)
            if profile.starting_monthly_investment is not None
            else None
        ),
        updated_at=profile.updated_at,
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

    payload_data = payload.model_dump(exclude_unset=existing is not None)
    user_updates = {k: payload_data.pop(k) for k in list(payload_data) if k in _USER_FIELDS}
    for field, value in user_updates.items():
        if field == "occupation" and value is not None:
            value = value.strip()[:100] if value.strip() else None
        setattr(user, field, value)

    if existing:
        for field, value in payload_data.items():
            if field in _PFP_FIELDS:
                setattr(existing, field, value)
        profile = existing
    else:
        profile = PersonalFinanceProfile(user_id=current_user.id, **payload_data)
        db.add(profile)

    await db.commit()
    await db.refresh(profile)
    try:
        await maybe_recalculate_effective_risk(db, current_user.id, "onboarding_profile_update")
        await db.commit()
    except Exception:
        await db.rollback()

    return _profile_to_response(user, profile)


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

    return _profile_to_response(user, profile)


@router.post("/other-assets", response_model=list[OtherAssetResponse])
async def save_other_assets(
    payload: OtherAssetBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await db.execute(
        delete(OtherInvestment).where(OtherInvestment.user_id == current_user.id)
    )
    rows = [
        OtherInvestment(
            user_id=current_user.id,
            investment_name=asset.asset_name,
            investment_type=asset.asset_type,
            present_value=asset.current_value or 0,
            status=OtherInvestmentStatus.ACTIVE,
        )
        for asset in payload.assets
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [_other_investment_to_legacy_response(r) for r in rows]


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_onboarding_complete = payload.is_complete
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
