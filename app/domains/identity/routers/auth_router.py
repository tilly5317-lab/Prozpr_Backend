"""FastAPI router — `auth.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.identity.services.signup_notification_service import notify_new_signup
from app.domains.profile.models import PersonalFinanceProfile
from app.domains.identity.models.user import User
from app.domains.identity.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    MobileLookupRequest,
    MobileStatusResponse,
    SignUpRequest,
    SignUpResponse,
    UserUpdateRequest,
    full_phone,
)
from app.core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


def _mid(lo: object, hi: object) -> float | None:
    vals = [float(v) for v in (lo, hi) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _inline_canonical_finance(
    payload: SignUpRequest | LoginRequest,
) -> dict[str, float]:
    """Map the optional inline income/expense ranges onto the canonical
    personal_finance_profiles columns (annual_income, monthly_household_expense).
    The model has no *_min/_max columns, so we collapse ranges to a midpoint."""
    data = payload.model_dump(exclude_unset=True)
    out: dict[str, float] = {}
    income = _mid(data.get("annual_income_min"), data.get("annual_income_max"))
    if income is not None:
        out["annual_income"] = income
    annual_expense = _mid(
        data.get("annual_expense_min"), data.get("annual_expense_max")
    )
    if annual_expense is not None:
        out["monthly_household_expense"] = annual_expense / 12.0
    return out


async def _save_inline_onboarding_profile(
    db: AsyncSession, user: User, payload: SignUpRequest | LoginRequest
) -> None:
    data = payload.model_dump(exclude_unset=True)
    date_of_birth = data.get("date_of_birth")
    if date_of_birth is not None:
        user.date_of_birth = date_of_birth

    finance = _inline_canonical_finance(payload)
    if not finance:
        return

    stmt = select(PersonalFinanceProfile).where(
        PersonalFinanceProfile.user_id == user.id
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = PersonalFinanceProfile(user_id=user.id, **finance)
        db.add(profile)
        return

    for field, value in finance.items():
        setattr(profile, field, value)


_EMAIL_TAKEN_DETAIL = "This email is already registered. Please sign in instead, or use a different email."


async def _email_taken(
    db: AsyncSession, email: str, exclude_user_id: uuid.UUID | None = None
) -> bool:
    """True if another user already owns this email (`users.email` is unique)."""
    stmt = select(User.id).where(User.email == email)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return (await db.execute(stmt)).first() is not None


def _identity_complete(user: User) -> bool:
    """A signup becomes 'reportable' once the user has both a name and an email
    — exactly the fields captured on the name / PIN / email setup page."""
    full_name = " ".join(
        p for p in (user.first_name or "", user.last_name or "") if p
    ).strip()
    return bool(full_name and user.email)


def _maybe_notify_new_signup(
    background_tasks: BackgroundTasks, user: User, was_complete_before: bool
) -> None:
    """Fire the one-time new-signup team notification (Slack + optional Sheet)
    the first time a user's identity becomes complete — i.e. the moment they
    submit the name / PIN / email page. Idempotent by design: a no-op if the
    identity was already complete before this request, or is still incomplete
    after it, so each user is reported exactly once. Best-effort background
    task: runs after the response, never fails the request."""
    if was_complete_before or not _identity_complete(user):
        return
    full_name = " ".join(
        p for p in (user.first_name or "", user.last_name or "") if p
    ).strip()
    background_tasks.add_task(
        notify_new_signup,
        datetime.now(timezone.utc),
        full_name,
        user.email,
        user.phone,
        "Signup",
    )


@router.post("/check-mobile", response_model=MobileStatusResponse)
async def check_mobile(
    payload: MobileLookupRequest, db: AsyncSession = Depends(get_db)
):
    phone = full_phone(payload.country_code, payload.mobile)
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if not user:
        return MobileStatusResponse(exists=False, is_onboarding_complete=False)
    return MobileStatusResponse(
        exists=True,
        is_onboarding_complete=user.is_onboarding_complete,
    )


@router.post(
    "/signup", response_model=SignUpResponse, status_code=status.HTTP_201_CREATED
)
async def signup(
    payload: SignUpRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    phone = full_phone(payload.country_code, payload.mobile)
    result = await db.execute(select(User).where(User.phone == phone))
    existing = result.scalar_one_or_none()
    if existing:
        # Whether the team was already told about this user — captured before we
        # apply the new identity fields so we can report exactly once, the first
        # time name + email land (see `_maybe_notify_new_signup`).
        was_complete_before = _identity_complete(existing)
        # A user actively setting their name / email / PIN must never be
        # dropped, even if a record for this phone already exists (e.g. one
        # created earlier via OTP). Persist the supplied identity fields.
        if payload.first_name is not None:
            existing.first_name = payload.first_name
        if payload.last_name is not None:
            existing.last_name = payload.last_name
        if payload.email is not None and payload.email != existing.email:
            if await _email_taken(db, payload.email, exclude_user_id=existing.id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN_DETAIL
                )
            existing.email = payload.email
        if payload.password and not existing.password_hash:
            existing.password_hash = hash_password(payload.password)
        await _save_inline_onboarding_profile(db, existing, payload)
        await db.commit()
        _maybe_notify_new_signup(background_tasks, existing, was_complete_before)
        access_token = create_access_token(existing.id, existing.phone)
        return SignUpResponse(
            user_id=existing.id,
            access_token=access_token,
            message="Account already exists. Logged in successfully.",
        )

    if payload.email and await _email_taken(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN_DETAIL
        )

    user = User(
        id=uuid.uuid4(),
        country_code=payload.country_code,
        mobile=payload.mobile,
        phone=phone,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        password_hash=hash_password(payload.password) if payload.password else None,
    )
    db.add(user)
    await _save_inline_onboarding_profile(db, user, payload)
    # Safety net for the rare race where the email is claimed between the check
    # above and this commit — surface a clean 409 instead of a 500.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN_DETAIL
        )
    await db.refresh(user)

    # Brand-new row, so nothing was reported before: notify the team now if the
    # setup page supplied a name + email (it always does on the happy path).
    _maybe_notify_new_signup(background_tasks, user, was_complete_before=False)

    access_token = create_access_token(user.id, user.phone)

    return SignUpResponse(
        user_id=user.id,
        access_token=access_token,
    )


async def _login_with_phone_password(
    phone: str, password: str | None, db: AsyncSession
) -> LoginResponse:
    if "," in phone:
        parts = phone.split(",", 1)
        full = full_phone(parts[0].strip(), parts[1].strip())
    else:
        v = phone.strip()
        digits = "".join(c for c in v if c.isdigit())
        full = "+" + digits if digits else ""

    if len(full) < 10:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    result = await db.execute(select(User).where(User.phone == full))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    # OTP-first mode: allow login by verified phone context without password.
    # If password is supplied and a hash exists, validate it for backward compatibility.
    if (
        password
        and user.password_hash
        and not verify_password(password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    access_token = create_access_token(user.id, user.phone)
    return LoginResponse(
        access_token=access_token,
        user_id=user.id,
    )


@router.post("/token", response_model=LoginResponse)
async def token(request: Request, db: AsyncSession = Depends(get_db)):
    content_type = (
        (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    )
    if content_type == "application/json":
        body = await request.json()
        payload = LoginRequest(**body)
        phone = full_phone(payload.country_code, payload.mobile)
        return await _login_with_phone_password(phone, payload.password, db)

    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Form must include username",
        )
    return await _login_with_phone_password(username, password, db)


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    phone = full_phone(payload.country_code, payload.mobile)
    response = await _login_with_phone_password(phone, payload.password, db)
    user_stmt = select(User).where(User.id == response.user_id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if user:
        await _save_inline_onboarding_profile(db, user, payload)
        await db.commit()
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def me(current_user: CurrentUser = Depends(get_current_user)):
    return CurrentUserResponse(
        id=current_user.id,
        country_code=current_user.country_code,
        mobile=current_user.mobile,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        is_onboarding_complete=current_user.is_onboarding_complete,
    )


@router.put("/me", response_model=CurrentUserResponse)
async def update_me(
    payload: UserUpdateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # The setup page calls this right after /signup as a safety net; report the
    # signup here too if name + email first become complete on this call (a
    # no-op when /signup already reported it — see `_maybe_notify_new_signup`).
    was_complete_before = _identity_complete(user)

    updates = payload.model_dump(exclude_unset=True)
    new_email = updates.get("email")
    if (
        new_email is not None
        and new_email != user.email
        and await _email_taken(db, new_email, exclude_user_id=user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN_DETAIL
        )

    for field, value in updates.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    _maybe_notify_new_signup(background_tasks, user, was_complete_before)

    return CurrentUserResponse(
        id=user.id,
        country_code=user.country_code,
        mobile=user.mobile,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_onboarding_complete=user.is_onboarding_complete,
    )
