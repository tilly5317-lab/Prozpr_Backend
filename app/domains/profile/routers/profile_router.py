"""FastAPI router — `profile.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.profile.models import (
    AssetAllocationConstraint,
    EffectiveRiskAssessment,
    InvestmentConstraint,
    InvestmentProfile,
    ReviewPreference,
    RiskProfile,
    TaxProfile,
    PersonalFinanceProfile,
    UserCurrentProperty,
)
from app.domains.identity.models.user import User
from app.domains.profile.schemas import (
    AllocationConstraintItem,
    CurrentPropertiesUpdate,
    CurrentPropertyResponse,
    FullProfileResponse,
    InvestmentConstraintResponse,
    InvestmentConstraintUpdate,
    InvestmentProfileResponse,
    InvestmentProfileUpdate,
    PersonalFinanceResponse,
    PersonalFinanceUpdate,
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
from app.domains.profile.services._effective_risk import (
    maybe_recalculate_effective_risk,
    upsert_effective_risk_assessment,
)
from app.domains.cashflow.services.cashflow_persist_service import (
    mark_stale as mark_cashflow_stale,
)

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/", response_model=FullProfileResponse)
async def get_full_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    uid = current_user.id
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    profile = (
        await db.execute(
            select(PersonalFinanceProfile).where(PersonalFinanceProfile.user_id == uid)
        )
    ).scalar_one_or_none()
    inv_profile = (
        await db.execute(
            select(InvestmentProfile)
            .options(selectinload(InvestmentProfile.current_properties))
            .where(InvestmentProfile.user_id == uid)
        )
    ).scalar_one_or_none()
    risk = (
        await db.execute(select(RiskProfile).where(RiskProfile.user_id == uid))
    ).scalar_one_or_none()
    constraint = (
        await db.execute(
            select(InvestmentConstraint).where(InvestmentConstraint.user_id == uid)
        )
    ).scalar_one_or_none()
    tax = (
        await db.execute(select(TaxProfile).where(TaxProfile.user_id == uid))
    ).scalar_one_or_none()
    review = (
        await db.execute(
            select(ReviewPreference).where(ReviewPreference.user_id == uid)
        )
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
                    "wealth_sources": (profile.wealth_sources or []) if profile else [],
                    "personal_values": (profile.personal_values or [])
                    if profile
                    else [],
                    "address": user.address if user else None,
                    "currency": user.currency if user else "INR",
                }
            )
            if user or profile
            else None
        ),
        investment_profile=InvestmentProfileResponse.model_validate(inv_profile)
        if inv_profile
        else None,
        risk_profile=RiskProfileResponse.model_validate(risk) if risk else None,
        investment_constraint=constraint_resp,
        tax_profile=TaxProfileResponse.model_validate(tax) if tax else None,
        review_preference=ReviewPreferenceResponse.model_validate(review)
        if review
        else None,
    )


# Section 1 - Personal Info
@router.get("/personal-info", response_model=PersonalInfoResponse)
async def get_personal_info(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    if not user and not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Personal info not found"
        )
    return PersonalInfoResponse.model_validate(
        {
            "occupation": user.occupation if user else None,
            "family_status": user.family_status if user else None,
            "wealth_sources": (profile.wealth_sources or []) if profile else [],
            "personal_values": (profile.personal_values or []) if profile else [],
            "address": user.address if user else None,
            "currency": user.currency if user else "INR",
        }
    )


@router.put("/personal-info", response_model=PersonalInfoResponse)
async def update_personal_info(
    payload: PersonalInfoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if not profile:
        profile = PersonalFinanceProfile(user_id=current_user.id)
        db.add(profile)

    payload_data = payload.model_dump(exclude_unset=True)
    # date_of_birth + assumed_lifespan_years live on `users`, not the finance
    # profile — route them to the user row so the cashflow engine can read them.
    for field in (
        "occupation",
        "family_status",
        "address",
        "currency",
        "date_of_birth",
        "assumed_lifespan_years",
    ):
        if field in payload_data:
            value = payload_data.pop(field)
            if value is not None:
                setattr(user, field, value)
    for field, value in payload_data.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    # DOB / assumed-lifespan feed the cashflow projection — invalidate the cached
    # plan run so the next fetch recomputes on the updated values.
    await mark_cashflow_stale(db, current_user.id)
    return PersonalInfoResponse.model_validate(
        {
            "occupation": user.occupation,
            "family_status": user.family_status,
            "wealth_sources": profile.wealth_sources or [],
            "personal_values": profile.personal_values or [],
            "address": user.address,
            "currency": user.currency,
            "date_of_birth": user.date_of_birth,
            "assumed_lifespan_years": user.assumed_lifespan_years,
        }
    )


# Household personal-finance scalars (personal_finance_profiles)
@router.get("/personal-finance", response_model=PersonalFinanceResponse)
async def get_personal_finance(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personal finance profile not found",
        )
    return PersonalFinanceResponse.model_validate(profile)


@router.put("/personal-finance", response_model=PersonalFinanceResponse)
async def update_personal_finance(
    payload: PersonalFinanceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == current_user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = PersonalFinanceProfile(user_id=current_user.id)
        db.add(profile)

    # exclude_unset → only the fields the client actually sent are written, so a
    # partial profile-edit never nulls out goals/other scalars it didn't touch.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    await maybe_recalculate_effective_risk(
        db, current_user.id, "personal_finance_update"
    )
    await db.commit()
    # Income / expense / assets / liabilities / tax / SIP are the core cashflow
    # inputs — invalidate the cached plan run so it recomputes on fresh values.
    await mark_cashflow_stale(db, current_user.id)
    return PersonalFinanceResponse.model_validate(profile)


# Section 2 + 4 + 6 - Investment Profile
@router.put("/investment", response_model=InvestmentProfileResponse)
async def update_investment_profile(
    payload: InvestmentProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(InvestmentProfile)
        .options(selectinload(InvestmentProfile.current_properties))
        .where(InvestmentProfile.user_id == current_user.id)
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = InvestmentProfile(user_id=current_user.id)
        db.add(profile)

    payload_data = payload.model_dump(exclude_unset=True)
    # current_properties is a relationship, not a scalar column — strip it
    # before the generic setattr loop. (Mutating that list here would also
    # need eager-loaded existing rows; the dedicated sub-resource endpoint
    # owns property writes.)
    payload_data.pop("current_properties", None)
    for field, value in payload_data.items():
        setattr(profile, field, value)

    await db.commit()
    # Re-fetch with the relationship eagerly loaded so Pydantic can serialise
    # without triggering implicit IO under expired attributes.
    profile = (await db.execute(stmt)).scalar_one()
    await maybe_recalculate_effective_risk(
        db, current_user.id, "investment_profile_update"
    )
    await db.commit()
    # retirement_age / target_corpus feed the cashflow projection — invalidate
    # the cached plan run so the next fetch recomputes.
    await mark_cashflow_stale(db, current_user.id)
    return InvestmentProfileResponse.model_validate(profile)


@router.get("/investment", response_model=InvestmentProfileResponse)
async def get_investment_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(InvestmentProfile)
        .options(selectinload(InvestmentProfile.current_properties))
        .where(InvestmentProfile.user_id == current_user.id)
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Investment profile not found"
        )
    return InvestmentProfileResponse.model_validate(profile)


# Owned properties (user_current_properties) — child of investment_profiles
@router.get("/current-properties", response_model=list[CurrentPropertyResponse])
async def get_current_properties(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(UserCurrentProperty).where(
        UserCurrentProperty.user_id == current_user.id
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [CurrentPropertyResponse.model_validate(r) for r in rows]


@router.put("/current-properties", response_model=list[CurrentPropertyResponse])
async def update_current_properties(
    payload: CurrentPropertiesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    uid = current_user.id
    # Ensure an investment profile exists so the rows surface via /investment's
    # current_properties relationship (joined on user_id).
    inv = (
        await db.execute(
            select(InvestmentProfile).where(InvestmentProfile.user_id == uid)
        )
    ).scalar_one_or_none()
    if not inv:
        db.add(InvestmentProfile(user_id=uid))

    # Full replace: drop the user's existing rows, then insert the new set.
    existing = await db.execute(
        select(UserCurrentProperty).where(UserCurrentProperty.user_id == uid)
    )
    for row in existing.scalars().all():
        await db.delete(row)
    await db.flush()

    created: list[UserCurrentProperty] = []
    for item in payload.properties:
        row = UserCurrentProperty(
            user_id=uid,
            name=item.name,
            property_value=item.property_value,
            has_mortgage=item.has_mortgage,
            mortgage_emi=item.mortgage_emi,
            mortgage_end_date=item.mortgage_end_date,
            mortgage_balance=item.mortgage_balance,
        )
        db.add(row)
        created.append(row)

    await db.commit()
    for row in created:
        await db.refresh(row)
    return [CurrentPropertyResponse.model_validate(r) for r in created]


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Risk profile not found"
        )
    return RiskProfileResponse.model_validate(profile)


@router.get("/effective-risk", response_model=EffectiveRiskAssessmentResponse)
async def get_effective_risk_assessment(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(EffectiveRiskAssessment).where(
        EffectiveRiskAssessment.user_id == current_user.id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Effective risk assessment not found",
        )
    return EffectiveRiskAssessmentResponse.model_validate(row)


@router.post(
    "/effective-risk/recalculate", response_model=EffectiveRiskRecalculateResponse
)
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
    stmt = select(InvestmentConstraint).where(
        InvestmentConstraint.user_id == current_user.id
    )
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
    stmt = select(InvestmentConstraint).where(
        InvestmentConstraint.user_id == current_user.id
    )
    constraint = (await db.execute(stmt)).scalar_one_or_none()
    if not constraint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investment constraints not found",
        )

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
    # income_tax_rate is a cashflow input (effective tax fallback) — invalidate
    # the cached plan run so it recomputes.
    await mark_cashflow_stale(db, current_user.id)
    return TaxProfileResponse.model_validate(profile)


@router.get("/tax", response_model=TaxProfileResponse)
async def get_tax_profile(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(TaxProfile).where(TaxProfile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tax profile not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review preferences not found"
        )
    return ReviewPreferenceResponse.model_validate(pref)
