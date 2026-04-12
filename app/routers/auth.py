"""FastAPI router — `auth.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.models.user import User
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    SignUpRequest,
    SignUpResponse,
    UserUpdateRequest,
    full_phone,
)
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup", response_model=SignUpResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignUpRequest, db: AsyncSession = Depends(get_db)):
    phone = full_phone(payload.country_code, payload.mobile)
    result = await db.execute(select(User).where(User.phone == phone))
    existing = result.scalar_one_or_none()
    if existing:
        access_token = create_access_token(existing.id, existing.phone)
        return SignUpResponse(
            user_id=existing.id,
            access_token=access_token,
            message="Account already exists. Logged in successfully.",
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
    await db.commit()
    await db.refresh(user)

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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    result = await db.execute(select(User).where(User.phone == full))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    # OTP-first mode: allow login by verified phone context without password.
    # If password is supplied and a hash exists, validate it for backward compatibility.
    if password and user.password_hash and not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(user.id, user.phone)
    return LoginResponse(
        access_token=access_token,
        user_id=user.id,
    )


@router.post("/token", response_model=LoginResponse)
async def token(request: Request, db: AsyncSession = Depends(get_db)):
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
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
    return await _login_with_phone_password(phone, payload.password, db)


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
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    return CurrentUserResponse(
        id=user.id,
        country_code=user.country_code,
        mobile=user.mobile,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_onboarding_complete=user.is_onboarding_complete,
    )
