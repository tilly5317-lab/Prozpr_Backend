"""FastAPI router — `profile.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.profile import (
    AssetAllocationConstraint,
    EffectiveRiskAssessment,
    InvestmentConstraint,
    InvestmentProfile,
    ReviewPreference,
    RiskProfile,
    TaxProfile,
    PersonalFinanceProfile,
)
from app.models.user import User
from app.schemas.profile import (
    AllocationConstraintItem,
    FullProfileResponse,
    InvestmentConstraintResponse,
    InvestmentConstraintUpdate,
    InvestmentProfileResponse,
    InvestmentProfileUpdate,
    PersonalInfoResponse,
    PersonalInfoUpdate,
    EffectiveRiskAssessmentResponse,
    EffectiveRiskRecalculateResponse,
    ReviewPreferenceResponse,
    ReviewPreferenceUpdate,
    RiskProfileResponse,
    RiskProfileUpdate,
    TaxProfileResponse,
    TaxProfileUpdate,
)
from app.services.effective_risk_profile import maybe_recalculate_effective_risk, upsert_effective_risk_assessment

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/", response_model=FullProfileResponse)
async def get_full_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    uid = current_user.id
    user = (
        await db.execute(select(User).where(User.id == uid))
    ).scalar_one_or_none()
    profile = (
        await db.execute(
            select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == uid)
        )
    ).scalar_one_or_none()
    inv_profile = (
        await db.execute(select(InvestmentProfile).where(InvestmentProfile.user_id == uid))
    ).scalar_one_or_none()
    risk = (
        await db.execute(select(RiskProfile).where(RiskProfile.user_id == uid))
    ).scalar_one_or_none()
    constraint = (
        await db.execute(select(InvestmentConstraint).where(InvestmentConstraint.user_id == uid))
    ).scalar_one_or_none()
    tax = (
        await db.execute(select(TaxProfile).where(TaxProfile.user_id == uid))
    ).scalar_one_or_none()
    review = (
        await db.execute(select(ReviewPreference).where(ReviewPreference.user_id == uid))
    ).scalar_one_or_none()

    constraint_resp = None
    if constraint:
        alloc_stmts = await db.execute(
            select(AssetAllocationConstraint).where(
                AssetAllocationConstraint.constraint_id == constraint.id
            )
        )
        constraint_resp = InvestmentConstraintResponse(
            id=constraint.id,
            permitted_assets=constraint.permitted_assets,
            prohibited_instruments=constraint.prohibited_instruments,
            is_leverage_allowed=constraint.is_leverage_allowed,
            is_derivatives_allowed=constraint.is_derivatives_allowed,
            diversification_notes=constraint.diversification_notes,
            allocation_constraints=[
                AllocationConstraintItem(
                    asset_class=ac.asset_class,
                    min_allocation=ac.min_allocation,
                    max_allocation=ac.max_allocation,
                )
                for ac in alloc_stmts.scalars().all()
            ],
        )

    return FullProfileResponse(
        personal_info=(
            PersonalInfoResponse.model_validate(
                {
                    "occupation": user.occupation if user else None,
                    "family_status": user.family_status if user else None,
                    "wealth_sources": profile.wealth_sources if profile else None,
                    "personal_values": profile.personal_values if profile else None,
                    "address": user.address if user else None,
                    "currency": user.currency if user else "GBP",
                }
            )
            if user or profile
            else None
        ),
        investment_profile=InvestmentProfileResponse.model_validate(inv_profile) if inv_profile else None,
        risk_profile=RiskProfileResponse.model_validate(risk) if risk else None,
        investment_constraint=constraint_resp,
        tax_profile=TaxProfileResponse.model_validate(tax) if tax else None,
        review_preference=ReviewPreferenceResponse.model_validate(review) if review else None,
    )


# Section 1 - Personal Info
@router.get("/personal-info", response_model=PersonalInfoResponse)
async def get_personal_info(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    if not user and not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personal info not found")
    return PersonalInfoResponse.model_validate(
        {
            "occupation": user.occupation if user else None,
            "family_status": user.family_status if user else None,
            "wealth_sources": profile.wealth_sources if profile else None,
            "personal_values": profile.personal_values if profile else None,
            "address": user.address if user else None,
            "currency": user.currency if user else "GBP",
        }
    )


@router.put("/personal-info", response_model=PersonalInfoResponse)
async def update_personal_info(
    payload: PersonalInfoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not profile:
        profile = PersonalFinanceProfile(user_id=current_user.id)
        db.add(profile)

    payload_data = payload.model_dump(exclude_unset=True)
    for field in ("occupation", "family_status", "address", "currency"):
        value = payload_data.pop(field, None)
        if value is not None:
            setattr(user, field, value)
    for field, value in payload_data.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    return PersonalInfoResponse.model_validate(
        {
            "occupation": user.occupation,
            "family_status": user.family_status,
            "wealth_sources": profile.wealth_sources,
            "personal_values": profile.personal_values,
            "address": user.address,
            "currency": user.currency,
        }
    )


# Section 2 + 4 + 6 - Investment Profile
@router.put("/investment", response_model=InvestmentProfileResponse)
async def update_investment_profile(
    payload: InvestmentProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(InvestmentProfile).where(InvestmentProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = InvestmentProfile(user_id=current_user.id)
        db.add(profile)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    await maybe_recalculate_effective_risk(db, current_user.id, "investment_profile_update")
    await db.commit()
    return InvestmentProfileResponse.model_validate(profile)


@router.get("/investment", response_model=InvestmentProfileResponse)
async def get_investment_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(InvestmentProfile).where(InvestmentProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investment profile not found")
    return InvestmentProfileResponse.model_validate(profile)


# Section 3 - Risk
@router.put("/risk", response_model=RiskProfileResponse)
async def update_risk_profile(
    payload: RiskProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(RiskProfile).where(RiskProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = RiskProfile(user_id=current_user.id)
        db.add(profile)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    await maybe_recalculate_effective_risk(db, current_user.id, "risk_profile_update")
    await db.commit()
    return RiskProfileResponse.model_validate(profile)


@router.get("/risk", response_model=RiskProfileResponse)
async def get_risk_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(RiskProfile).where(RiskProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk profile not found")
    return RiskProfileResponse.model_validate(profile)


@router.get("/effective-risk", response_model=EffectiveRiskAssessmentResponse)
async def get_effective_risk_assessment(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(EffectiveRiskAssessment).where(EffectiveRiskAssessment.user_id == current_user.id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Effective risk assessment not found")
    return EffectiveRiskAssessmentResponse.model_validate(row)


@router.post("/effective-risk/recalculate", response_model=EffectiveRiskRecalculateResponse)
async def recalculate_effective_risk_endpoint(
    as_of: Optional[date] = Query(
        None,
        description="Optional ‘as of’ date for age (e.g. birthday batch runs). Defaults to today.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    out = await upsert_effective_risk_assessment(
        db, current_user.id, trigger_reason="manual", as_of=as_of
    )
    await db.commit()
    if out is None:
        return EffectiveRiskRecalculateResponse(
            updated=False,
            detail="Could not compute effective risk (e.g. date of birth is missing).",
        )
    return EffectiveRiskRecalculateResponse(
        updated=True,
        assessment=EffectiveRiskAssessmentResponse.model_validate(out),
    )


# Section 5 - Constraints
@router.put("/constraints", response_model=InvestmentConstraintResponse)
async def update_investment_constraints(
    payload: InvestmentConstraintUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(InvestmentConstraint).where(InvestmentConstraint.user_id == current_user.id)
    constraint = (await db.execute(stmt)).scalar_one_or_none()
    if not constraint:
        constraint = InvestmentConstraint(user_id=current_user.id)
        db.add(constraint)
        await db.flush()

    data = payload.model_dump(exclude_unset=True, exclude={"allocation_constraints"})
    for field, value in data.items():
        setattr(constraint, field, value)

    if payload.allocation_constraints is not None:
        existing = await db.execute(
            select(AssetAllocationConstraint).where(
                AssetAllocationConstraint.constraint_id == constraint.id
            )
        )
        for ac in existing.scalars().all():
            await db.delete(ac)

        for item in payload.allocation_constraints:
            ac = AssetAllocationConstraint(
                constraint_id=constraint.id,
                asset_class=item.asset_class,
                min_allocation=item.min_allocation,
                max_allocation=item.max_allocation,
            )
            db.add(ac)

    await db.commit()
    await db.refresh(constraint)

    alloc_stmts = await db.execute(
        select(AssetAllocationConstraint).where(
            AssetAllocationConstraint.constraint_id == constraint.id
        )
    )
    return InvestmentConstraintResponse(
        id=constraint.id,
        permitted_assets=constraint.permitted_assets,
        prohibited_instruments=constraint.prohibited_instruments,
        is_leverage_allowed=constraint.is_leverage_allowed,
        is_derivatives_allowed=constraint.is_derivatives_allowed,
        diversification_notes=constraint.diversification_notes,
        allocation_constraints=[
            AllocationConstraintItem(
                asset_class=ac.asset_class,
                min_allocation=ac.min_allocation,
                max_allocation=ac.max_allocation,
            )
            for ac in alloc_stmts.scalars().all()
        ],
    )


@router.get("/constraints", response_model=InvestmentConstraintResponse)
async def get_investment_constraints(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(InvestmentConstraint).where(InvestmentConstraint.user_id == current_user.id)
    constraint = (await db.execute(stmt)).scalar_one_or_none()
    if not constraint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investment constraints not found")

    alloc_stmts = await db.execute(
        select(AssetAllocationConstraint).where(
            AssetAllocationConstraint.constraint_id == constraint.id
        )
    )
    return InvestmentConstraintResponse(
        id=constraint.id,
        permitted_assets=constraint.permitted_assets,
        prohibited_instruments=constraint.prohibited_instruments,
        is_leverage_allowed=constraint.is_leverage_allowed,
        is_derivatives_allowed=constraint.is_derivatives_allowed,
        diversification_notes=constraint.diversification_notes,
        allocation_constraints=[
            AllocationConstraintItem(
                asset_class=ac.asset_class,
                min_allocation=ac.min_allocation,
                max_allocation=ac.max_allocation,
            )
            for ac in alloc_stmts.scalars().all()
        ],
    )


# Section 7 - Tax
@router.put("/tax", response_model=TaxProfileResponse)
async def update_tax_profile(
    payload: TaxProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(TaxProfile).where(TaxProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = TaxProfile(user_id=current_user.id)
        db.add(profile)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    return TaxProfileResponse.model_validate(profile)


@router.get("/tax", response_model=TaxProfileResponse)
async def get_tax_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(TaxProfile).where(TaxProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tax profile not found")
    return TaxProfileResponse.model_validate(profile)


# Section 8 - Review
@router.put("/review", response_model=ReviewPreferenceResponse)
async def update_review_preference(
    payload: ReviewPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(ReviewPreference).where(ReviewPreference.user_id == current_user.id)
    pref = (await db.execute(stmt)).scalar_one_or_none()
    if not pref:
        pref = ReviewPreference(user_id=current_user.id)
        db.add(pref)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pref, field, value)

    await db.commit()
    await db.refresh(pref)
    return ReviewPreferenceResponse.model_validate(pref)


@router.get("/review", response_model=ReviewPreferenceResponse)
async def get_review_preference(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(ReviewPreference).where(ReviewPreference.user_id == current_user.id)
    pref = (await db.execute(stmt)).scalar_one_or_none()
    if not pref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review preferences not found")
    return ReviewPreferenceResponse.model_validate(pref)
