"""FastAPI router — `ips.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.ips import InvestmentPolicyStatement
from app.models.profile import (
    AssetAllocationConstraint,
    InvestmentConstraint,
    InvestmentProfile,
    ReviewPreference,
    RiskProfile,
    TaxProfile,
    PersonalFinanceProfile,
)
from app.models.user import User
from app.schemas.ips import IPSListResponse, IPSResponse

router = APIRouter(prefix="/ips", tags=["Investment Policy Statement"])


@router.get("/", response_model=IPSResponse)
async def get_current_ips(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(InvestmentPolicyStatement)
        .where(
            InvestmentPolicyStatement.user_id == current_user.id,
            InvestmentPolicyStatement.status == "active",
        )
        .order_by(InvestmentPolicyStatement.version.desc())
        .limit(1)
    )
    ips = (await db.execute(stmt)).scalar_one_or_none()
    if not ips:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active IPS found")
    return IPSResponse.model_validate(ips)


@router.post("/generate", response_model=IPSResponse, status_code=status.HTTP_201_CREATED)
async def generate_ips(
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
    inv_profile = (await db.execute(select(InvestmentProfile).where(InvestmentProfile.user_id == uid))).scalar_one_or_none()
    risk = (await db.execute(select(RiskProfile).where(RiskProfile.user_id == uid))).scalar_one_or_none()
    constraint = (await db.execute(select(InvestmentConstraint).where(InvestmentConstraint.user_id == uid))).scalar_one_or_none()
    tax = (await db.execute(select(TaxProfile).where(TaxProfile.user_id == uid))).scalar_one_or_none()
    review = (await db.execute(select(ReviewPreference).where(ReviewPreference.user_id == uid))).scalar_one_or_none()

    alloc_constraints = []
    if constraint:
        alloc_result = await db.execute(
            select(AssetAllocationConstraint).where(
                AssetAllocationConstraint.constraint_id == constraint.id
            )
        )
        alloc_constraints = [
            {"asset_class": ac.asset_class, "min": ac.min_allocation, "max": ac.max_allocation}
            for ac in alloc_result.scalars().all()
        ]

    content = {
        "personal_background": {
            "occupation": user.occupation if user else None,
            "family_status": user.family_status if user else None,
            "wealth_sources": profile.wealth_sources if profile else None,
            "personal_values": profile.personal_values if profile else None,
        },
        "return_objectives": {
            "objectives": inv_profile.objectives if inv_profile else None,
            "detailed_goals": inv_profile.detailed_goals if inv_profile else None,
            "portfolio_value": float(inv_profile.portfolio_value) if inv_profile and inv_profile.portfolio_value else None,
            "monthly_savings": float(inv_profile.monthly_savings) if inv_profile and inv_profile.monthly_savings else None,
            "target_corpus": float(inv_profile.target_corpus) if inv_profile and inv_profile.target_corpus else None,
            "target_timeline": inv_profile.target_timeline if inv_profile else None,
            "annual_income": float(inv_profile.annual_income) if inv_profile and inv_profile.annual_income else None,
            "retirement_age": inv_profile.retirement_age if inv_profile else None,
        },
        "risk_tolerance": {
            "risk_level": risk.risk_level if risk else None,
            "investment_horizon": risk.investment_horizon if risk else None,
            "drop_reaction": risk.drop_reaction if risk else None,
            "max_drawdown": float(risk.max_drawdown) if risk and risk.max_drawdown else None,
            "comfort_assets": risk.comfort_assets if risk else None,
        },
        "financial_situation": {
            "investable_assets": float(inv_profile.investable_assets) if inv_profile and inv_profile.investable_assets else None,
            "total_liabilities": float(inv_profile.total_liabilities) if inv_profile and inv_profile.total_liabilities else None,
            "property_value": float(inv_profile.property_value) if inv_profile and inv_profile.property_value else None,
            "emergency_fund": float(inv_profile.emergency_fund) if inv_profile and inv_profile.emergency_fund else None,
        },
        "investment_constraints": {
            "permitted_assets": constraint.permitted_assets if constraint else None,
            "prohibited_instruments": constraint.prohibited_instruments if constraint else None,
            "leverage_allowed": constraint.is_leverage_allowed if constraint else None,
            "derivatives_allowed": constraint.is_derivatives_allowed if constraint else None,
            "allocation_constraints": alloc_constraints,
        },
        "time_horizon": {
            "is_multi_phase": inv_profile.is_multi_phase_horizon if inv_profile else None,
            "phase_description": inv_profile.phase_description if inv_profile else None,
            "total_horizon": inv_profile.total_horizon if inv_profile else None,
        },
        "tax_situation": {
            "income_tax_rate": float(tax.income_tax_rate) if tax and tax.income_tax_rate else None,
            "capital_gains_tax_rate": float(tax.capital_gains_tax_rate) if tax and tax.capital_gains_tax_rate else None,
            "notes": tax.notes if tax else None,
        },
        "review_process": {
            "frequency": review.frequency if review else None,
            "triggers": review.triggers if review else None,
            "update_process": review.update_process if review else None,
        },
    }

    version_count = (
        await db.execute(
            select(func.count(InvestmentPolicyStatement.id)).where(
                InvestmentPolicyStatement.user_id == uid
            )
        )
    ).scalar() or 0

    ips = InvestmentPolicyStatement(
        user_id=uid,
        version=version_count + 1,
        status="active",
        content=content,
    )
    db.add(ips)
    await db.commit()
    await db.refresh(ips)
    return IPSResponse.model_validate(ips)


@router.get("/history", response_model=IPSListResponse)
async def list_ips_history(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(InvestmentPolicyStatement)
        .where(InvestmentPolicyStatement.user_id == current_user.id)
        .order_by(InvestmentPolicyStatement.version.desc())
    )
    result = await db.execute(stmt)
    statements = [IPSResponse.model_validate(s) for s in result.scalars().all()]
    return IPSListResponse(statements=statements)
