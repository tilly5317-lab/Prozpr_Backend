"""FastAPI router — `family.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.models.family_member import FamilyMember
from app.models.portfolio import Portfolio
from app.models.user import User
from app.schemas.family import (
    AddFamilyMemberRequest,
    CumulativeAllocationItem,
    CumulativePortfolioResponse,
    FamilyMemberListResponse,
    FamilyMemberPortfolioSummary,
    FamilyMemberResponse,
    OnboardFamilyMemberRequest,
    UpdateFamilyMemberRequest,
    VerifyFamilyOtpRequest,
    ResendFamilyOtpRequest,
    OtpSentResponse,
)
from app.schemas.portfolio import (
    PortfolioAllocationResponse,
    PortfolioDetailResponse,
    PortfolioHoldingResponse,
    PortfolioResponse,
)
from app.services.otp_service import send_otp, verify_otp, resend_otp as resend_otp_svc
from app.utils.security import hash_password
from app.schemas.auth import full_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/family", tags=["Family"])


def _build_member_response(fm: FamilyMember) -> FamilyMemberResponse:
    member_first = None
    member_last = None
    initials = None
    if fm.member_user:
        member_first = fm.member_user.first_name
        member_last = fm.member_user.last_name
        f = (member_first or "")[:1].upper()
        l = (member_last or "")[:1].upper()
        initials = (f + l) or fm.nickname[:1].upper()
    else:
        initials = fm.nickname[:1].upper()

    return FamilyMemberResponse(
        id=fm.id,
        owner_id=fm.owner_id,
        member_user_id=fm.member_user_id,
        nickname=fm.nickname,
        email=fm.email,
        phone=fm.phone,
        relationship_type=fm.relationship_type,
        status=fm.status,
        member_first_name=member_first,
        member_last_name=member_last,
        member_initials=initials,
        created_at=fm.created_at,
        updated_at=fm.updated_at,
    )


# ── Step 1: Initiate — creates pending_otp record & sends OTP ───────────

@router.post("/members", response_model=FamilyMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_family_member(
    payload: AddFamilyMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Link an existing user as a family member.  Sends OTP for consent.
    Returns 404 with code 'member_not_found' if no account exists for
    the given phone so the client can offer onboarding instead."""

    if not payload.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number is required for OTP verification",
        )

    # Match User.phone which is stored as full E.164 (e.g. +918468882140 from signup), not raw 10 digits.
    raw = payload.phone.strip()
    if raw.startswith("+"):
        phone_full = raw
    else:
        phone_full = full_phone(payload.country_code, raw)

    linked_user: User | None = None
    conditions = [User.phone == phone_full]
    if payload.email:
        conditions.append(User.email == payload.email)
    result = await db.execute(select(User).where(or_(*conditions)))
    linked_user = result.scalar_one_or_none()

    if not linked_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "member_not_found",
                "message": "No account found for this phone number. You can onboard your family member instead.",
            },
        )

    if linked_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add yourself as a family member",
        )

    existing = await db.execute(
        select(FamilyMember).where(
            FamilyMember.owner_id == current_user.id,
            FamilyMember.member_user_id == linked_user.id,
            FamilyMember.status.in_(["active", "pending_otp"]),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user is already in your family or has a pending verification",
        )

    fm = FamilyMember(
        owner_id=current_user.id,
        member_user_id=linked_user.id,
        nickname=payload.nickname.strip(),
        email=payload.email,
        phone=phone_full,
        relationship_type=payload.relationship_type,
        status="pending_otp",
    )
    db.add(fm)
    await db.commit()

    country_code, mobile = _split_phone(phone_full)
    try:
        await send_otp(country_code, mobile)
    except Exception as e:
        logger.error("Failed to send OTP for family member %s: %s", fm.id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send OTP. Please try again.",
        )

    stmt = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(FamilyMember.id == fm.id)
    )
    fm = (await db.execute(stmt)).scalar_one()
    return _build_member_response(fm)


# ── Onboard — register a new user and create the family link ─────────────

@router.post("/members/onboard", response_model=FamilyMemberResponse, status_code=status.HTTP_201_CREATED)
async def onboard_family_member(
    payload: OnboardFamilyMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a brand-new user account for a family member who doesn't have
    one yet, create the family link, and send OTP for consent verification."""

    phone_full = full_phone(payload.country_code, payload.phone)

    existing_user = await db.execute(select(User).where(User.phone == phone_full))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this phone number. Use the regular add flow instead.",
        )

    new_user = User(
        id=uuid.uuid4(),
        country_code=payload.country_code,
        mobile=payload.phone,
        phone=phone_full,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        password_hash=hash_password(payload.password),
    )
    db.add(new_user)
    await db.flush()

    fm = FamilyMember(
        owner_id=current_user.id,
        member_user_id=new_user.id,
        nickname=payload.nickname.strip(),
        email=payload.email,
        phone=phone_full,
        relationship_type=payload.relationship_type,
        status="pending_otp",
    )
    db.add(fm)
    await db.commit()

    country_code, mobile = _split_phone(phone_full)
    try:
        await send_otp(country_code, mobile)
    except Exception as e:
        logger.error("Failed to send OTP for onboarded family member %s: %s", fm.id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Account created but failed to send OTP. Try resending.",
        )

    stmt = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(FamilyMember.id == fm.id)
    )
    fm = (await db.execute(stmt)).scalar_one()
    return _build_member_response(fm)


# ── Step 2: Verify OTP — activates the link ──────────────────────────────

@router.post("/members/{member_id}/verify-otp", response_model=FamilyMemberResponse)
async def verify_family_otp(
    member_id: uuid.UUID,
    payload: VerifyFamilyOtpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    stmt = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(
            FamilyMember.id == member_id,
            FamilyMember.owner_id == current_user.id,
            FamilyMember.status == "pending_otp",
        )
    )
    fm = (await db.execute(stmt)).scalar_one_or_none()
    if not fm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending family member not found",
        )

    if not fm.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No phone number associated with this member",
        )

    country_code, mobile = _split_phone(fm.phone)
    try:
        await verify_otp(country_code, mobile, payload.otp)
    except Exception as e:
        logger.warning("OTP verification failed for family member %s: %s", fm.id, e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP",
        )

    fm.status = "active"

    if not fm.member_user_id:
        user_result = await db.execute(
            select(User).where(
                or_(User.phone == fm.phone, User.email == fm.email) if fm.email
                else User.phone == fm.phone
            )
        )
        found_user = user_result.scalar_one_or_none()
        if found_user:
            fm.member_user_id = found_user.id

    await db.commit()
    await db.refresh(fm)

    stmt2 = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(FamilyMember.id == fm.id)
    )
    fm = (await db.execute(stmt2)).scalar_one()
    return _build_member_response(fm)


# ── Resend OTP ───────────────────────────────────────────────────────────

@router.post("/members/{member_id}/resend-otp", response_model=OtpSentResponse)
async def resend_family_otp(
    member_id: uuid.UUID,
    payload: ResendFamilyOtpRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    stmt = select(FamilyMember).where(
        FamilyMember.id == member_id,
        FamilyMember.owner_id == current_user.id,
        FamilyMember.status == "pending_otp",
    )
    fm = (await db.execute(stmt)).scalar_one_or_none()
    if not fm or not fm.phone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending family member not found",
        )

    country_code, mobile = _split_phone(fm.phone)
    retry_type = payload.retry_type if payload else "text"
    try:
        await resend_otp_svc(country_code, mobile, retry_type)
    except Exception as e:
        logger.error("Failed to resend OTP for family member %s: %s", fm.id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to resend OTP",
        )

    return OtpSentResponse()


# ── CRUD ─────────────────────────────────────────────────────────────────

@router.get("/members", response_model=FamilyMemberListResponse)
async def list_family_members(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    stmt = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(FamilyMember.owner_id == current_user.id)
        .order_by(FamilyMember.created_at.asc())
    )
    result = await db.execute(stmt)
    members = result.scalars().all()

    return FamilyMemberListResponse(
        members=[_build_member_response(m) for m in members],
        count=len(members),
    )


@router.put("/members/{member_id}", response_model=FamilyMemberResponse)
async def update_family_member(
    member_id: uuid.UUID,
    payload: UpdateFamilyMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    stmt = (
        select(FamilyMember)
        .options(selectinload(FamilyMember.member_user))
        .where(FamilyMember.id == member_id, FamilyMember.owner_id == current_user.id)
    )
    fm = (await db.execute(stmt)).scalar_one_or_none()
    if not fm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family member not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(fm, field, value)

    await db.commit()
    await db.refresh(fm)
    return _build_member_response(fm)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_family_member(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    stmt = select(FamilyMember).where(
        FamilyMember.id == member_id,
        FamilyMember.owner_id == current_user.id,
    )
    fm = (await db.execute(stmt)).scalar_one_or_none()
    if not fm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family member not found")

    await db.delete(fm)
    await db.commit()


# ── Member portfolio (read-only, no header needed) ───────────────────────

@router.get("/members/{member_id}/portfolio", response_model=PortfolioDetailResponse)
async def get_member_portfolio(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    fm_stmt = select(FamilyMember).where(
        FamilyMember.id == member_id,
        FamilyMember.owner_id == current_user.id,
        FamilyMember.status == "active",
    )
    fm = (await db.execute(fm_stmt)).scalar_one_or_none()
    if not fm or not fm.member_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active linked family member not found",
        )

    stmt = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.allocations),
            selectinload(Portfolio.holdings),
        )
        .where(Portfolio.user_id == fm.member_user_id, Portfolio.is_primary)
    )
    portfolio = (await db.execute(stmt)).scalar_one_or_none()
    if not portfolio:
        from datetime import datetime, timezone
        return PortfolioDetailResponse(
            id=uuid.uuid4(),
            name="Primary",
            total_value=0,
            total_invested=0,
            total_gain_percentage=None,
            is_primary=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            allocations=[],
            holdings=[],
        )

    return PortfolioDetailResponse(
        **PortfolioResponse.model_validate(portfolio).model_dump(),
        allocations=[PortfolioAllocationResponse.model_validate(a) for a in portfolio.allocations],
        holdings=[PortfolioHoldingResponse.model_validate(h) for h in portfolio.holdings],
    )


# ── Cumulative portfolio ─────────────────────────────────────────────────

@router.get("/portfolio/cumulative", response_model=CumulativePortfolioResponse)
async def get_cumulative_portfolio(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Aggregate portfolios of the owner + all active family members."""
    fm_stmt = select(FamilyMember).where(
        FamilyMember.owner_id == current_user.id,
        FamilyMember.status == "active",
        FamilyMember.member_user_id.isnot(None),
    )
    active_members = (await db.execute(fm_stmt)).scalars().all()

    user_ids = [current_user.id] + [m.member_user_id for m in active_members]

    portfolios_stmt = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.allocations),
            selectinload(Portfolio.holdings),
        )
        .where(Portfolio.user_id.in_(user_ids), Portfolio.is_primary)
    )
    portfolios = (await db.execute(portfolios_stmt)).scalars().all()

    portfolio_by_user: dict[uuid.UUID, Portfolio] = {p.user_id: p for p in portfolios}

    member_summaries: list[FamilyMemberPortfolioSummary] = []

    owner_portfolio = portfolio_by_user.get(current_user.id)
    owner_value = float(owner_portfolio.total_value) if owner_portfolio else 0
    owner_invested = float(owner_portfolio.total_invested) if owner_portfolio else 0
    member_summaries.append(
        FamilyMemberPortfolioSummary(
            member_id=current_user.id,
            nickname="You",
            relationship_type="self",
            portfolio_value=owner_value,
            total_invested=owner_invested,
            gain_percentage=float(owner_portfolio.total_gain_percentage) if owner_portfolio and owner_portfolio.total_gain_percentage else None,
        )
    )

    for fm in active_members:
        p = portfolio_by_user.get(fm.member_user_id)
        member_summaries.append(
            FamilyMemberPortfolioSummary(
                member_id=fm.id,
                nickname=fm.nickname,
                relationship_type=fm.relationship_type,
                portfolio_value=float(p.total_value) if p else 0,
                total_invested=float(p.total_invested) if p else 0,
                gain_percentage=float(p.total_gain_percentage) if p and p.total_gain_percentage else None,
            )
        )

    total_value = sum(m.portfolio_value for m in member_summaries)
    total_invested = sum(m.total_invested for m in member_summaries)
    total_gain_pct = (
        round(((total_value - total_invested) / total_invested) * 100, 2)
        if total_invested > 0
        else None
    )

    allocation_map: dict[str, float] = {}
    for p in portfolios:
        for alloc in p.allocations:
            key = alloc.asset_class
            allocation_map[key] = allocation_map.get(key, 0) + float(alloc.amount)

    combined_allocs = []
    for asset_class, amount in sorted(allocation_map.items(), key=lambda x: -x[1]):
        pct = round((amount / total_value) * 100, 2) if total_value > 0 else 0
        combined_allocs.append(
            CumulativeAllocationItem(
                asset_class=asset_class,
                total_amount=amount,
                allocation_percentage=pct,
            )
        )

    return CumulativePortfolioResponse(
        total_value=total_value,
        total_invested=total_invested,
        total_gain_percentage=total_gain_pct,
        member_count=len(member_summaries),
        members=member_summaries,
        combined_allocations=combined_allocs,
    )


# ── Helpers ──────────────────────────────────────────────────────────────

def _split_phone(phone: str) -> tuple[str, str]:
    """Split a full phone like '+919876543210' into (country_code, mobile).
    Assumes the first 1-3 digits after '+' are the country code."""
    cleaned = phone.strip().lstrip("+")
    if cleaned.startswith("91") and len(cleaned) >= 12:
        return "+91", cleaned[2:]
    if cleaned.startswith("1") and len(cleaned) >= 11:
        return "+1", cleaned[1:]
    if len(cleaned) > 10:
        return "+" + cleaned[:2], cleaned[2:]
    return "+91", cleaned
