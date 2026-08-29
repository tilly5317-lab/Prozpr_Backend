"""FastAPI dependency providers shared across routers.

Includes JWT bearer auth (``get_current_user``), optional family-member impersonation via
``X-Family-Member-Id`` (``get_effective_user``), and ``get_ai_user_context`` which loads a
full ``User`` ORM graph for chat and allocation (profiles, goals, portfolios).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import resolve_active_cas_upload_id, set_scope
from app.core.database import get_db
from app.domains.identity.models.user import User
from app.domains.identity.services.user_context_loader import load_user_for_ai
from app.core.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=True)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    country_code: str
    mobile: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_active: bool = True
    is_onboarding_complete: bool = False
    # None unless the user chose "I'll do this later" on the CAMS onboarding
    # step. Carried here (rather than re-queried) because the User row is
    # already loaded — every surface that needs "did they skip CAMS?" reads it
    # off /auth/me for free.
    cams_skipped_at: Optional[datetime] = None
    # Carried off the already-loaded User row so /auth/me can report a masked
    # PAN and any in-flight step-up change without a second query. The FULL pan
    # value lives here but is never serialised by /auth/me — see
    # `_current_user_response` in auth_router.
    pan: Optional[str] = None
    sensitive_change_field: Optional[str] = None
    avatar_key: Optional[str] = None


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if not token or token.strip().lower() in {"null", "undefined"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token.strip())
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = uuid.UUID(user_id_str)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # A user who has asked to be erased stops being processed immediately, even
    # though their row survives the 30-day grace window. Existing JWTs live for
    # 7 days, so without this check a "deleted" account keeps working for a week
    # and could cancel its own erasure. 401 rather than 403: the token is no
    # longer valid, and the client's existing sign-out path handles it.
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account has been deleted.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Tag the request so a later 5xx can be attributed to this user without the
    # exception handler needing DB access. Read in app/core/exceptions.py.
    request.state.distinct_id = str(user.id)
    # Pin every query in this request to the user's live CAS statement. One
    # indexed lookup here is what lets ~56 read sites stay untouched — see
    # app/core/cas_scope.py. get_effective_user re-pins it for family members.
    set_scope(await resolve_active_cas_upload_id(db, user.id))
    return CurrentUser(
        id=user.id,
        country_code=user.country_code,
        mobile=user.mobile,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_active=user.is_active,
        is_onboarding_complete=user.is_onboarding_complete,
        cams_skipped_at=user.cams_skipped_at,
        pan=user.pan,
        sensitive_change_field=user.sensitive_change_field,
        avatar_key=user.avatar_key,
    )


async def get_effective_user(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_family_member_id: Optional[str] = Header(default=None),
) -> CurrentUser:
    """Return the family member's identity when the X-Family-Member-Id header
    is present and the caller owns a verified (active) link to that member.
    Otherwise return the caller's own identity unchanged.

    This lets every existing endpoint work for family members transparently —
    the owner authenticates with their JWT, the header picks who they act as.
    """
    if not x_family_member_id:
        return current_user

    from app.domains.identity.models.family_member import FamilyMember

    try:
        member_id = uuid.UUID(x_family_member_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Family-Member-Id header",
        )

    fm_result = await db.execute(
        select(FamilyMember).where(
            FamilyMember.id == member_id,
            FamilyMember.owner_id == current_user.id,
            FamilyMember.status == "active",
        )
    )
    fm = fm_result.scalar_one_or_none()
    if not fm or not fm.member_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have active access to this family member",
        )

    user_result = await db.execute(select(User).where(User.id == fm.member_user_id))
    member_user = user_result.scalar_one_or_none()
    if not member_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Family member's account not found",
        )

    # Acting as someone else means reading THEIR statement, not the caller's.
    set_scope(await resolve_active_cas_upload_id(db, member_user.id))
    return CurrentUser(
        id=member_user.id,
        country_code=member_user.country_code,
        mobile=member_user.mobile,
        email=member_user.email,
        first_name=member_user.first_name,
        last_name=member_user.last_name,
        is_active=member_user.is_active,
        is_onboarding_complete=member_user.is_onboarding_complete,
        cams_skipped_at=member_user.cams_skipped_at,
        pan=member_user.pan,
        sensitive_change_field=member_user.sensitive_change_field,
        avatar_key=member_user.avatar_key,
    )


async def get_ai_user_context(
    current_user: CurrentUser = Depends(get_effective_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """ORM User with profile, risk, portfolios, etc., for AI module routes."""
    user = await load_user_for_ai(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user
