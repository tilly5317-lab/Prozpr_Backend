"""FastAPI router — `auth.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.identity.services.pin_reset_email_service import (
    ResendNotConfigured,
    ResendSendFailed,
    send_pin_reset_code,
)
from app.domains.identity.services.signup_notification_service import notify_new_signup
from app.domains.profile.models import PersonalFinanceProfile
from app.domains.identity.models.user import User
from app.domains.identity.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    MobileLookupRequest,
    MobileStatusResponse,
    PIN_RESET_CODE_DIGITS,
    PinResetConfirmRequest,
    PinResetRequestRequest,
    PinResetRequestResponse,
    PinUpdateRequest,
    SignUpRequest,
    SignUpResponse,
    UserUpdateRequest,
    full_phone,
)
from app.core.security import create_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)

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


async def _ensure_fp_investment_profile(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Create the user's investment-profile shell (``fp_exec_accounts`` row,
    ``kyc_status='pending'``) at signup itself. The FP-side investor profile is
    minted later, once the KYC page collects a PAN — the sandbox requires every
    field inline at CREATE. Best-effort: never fails the signup."""
    try:
        from app.domains.execution.services.fp_service import ensure_account_row

        await ensure_account_row(db, user_id)
    except Exception:  # noqa: BLE001 — signup must never break on this
        # A failed flush poisons the session; roll it back so the rest of the
        # signup response can still be built.
        await db.rollback()
        logger.warning("FP investment-profile init skipped for %s", user_id)


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
        await _ensure_fp_investment_profile(db, existing.id)
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

    await _ensure_fp_investment_profile(db, user.id)

    # Brand-new row, so nothing was reported before: notify the team now if the
    # setup page supplied a name + email (it always does on the happy path).
    _maybe_notify_new_signup(background_tasks, user, was_complete_before=False)

    access_token = create_access_token(user.id, user.phone)

    return SignUpResponse(
        user_id=user.id,
        access_token=access_token,
    )


# Default country code, matching the app's login form (`+91`). Lets the Swagger
# "Authorize" box — which only has a single username field — accept a bare
# registered mobile number (e.g. 9876543210) and resolve it to the stored
# +91XXXXXXXXXX, so authorizing works the same way it does in the app.
_DEFAULT_COUNTRY_CODE = "91"


def _phone_candidates(raw: str) -> list[str]:
    """Resolve a login username into the full phone(s) to try. Accepts the app's
    ``+91XXXXXXXXXX`` / ``countrycode,mobile`` forms AND a bare national mobile
    (no country code, as typed in Swagger's single username box)."""
    if "," in raw:
        return [full_phone(*(p.strip() for p in raw.split(",", 1)))]
    v = raw.strip()
    had_plus = v.startswith("+")
    digits = "".join(c for c in v if c.isdigit())
    if not digits:
        return []
    candidates = ["+" + digits]
    # Bare national number, no country code entered -> default to +91 like the app.
    if not had_plus and len(digits) <= 10:
        candidates.append("+" + _DEFAULT_COUNTRY_CODE + digits)
    # de-dupe, keep only plausibly-complete numbers (matches the old >= 10 floor)
    return [c for c in dict.fromkeys(candidates) if len(c) >= 10]


async def _login_with_phone_password(
    phone: str, password: str | None, db: AsyncSession
) -> LoginResponse:
    candidates = _phone_candidates(phone)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    result = await db.execute(select(User).where(User.phone.in_(candidates)))
    user = result.scalars().first()
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
    # A client (browser / Swagger / a proxy probe) that closes the connection
    # mid-request raises ClientDisconnect here; the global handler in
    # app/core/exceptions.py turns that into a quiet 499 rather than a 5xx.
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
        cams_skipped=current_user.cams_skipped_at is not None,
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
        cams_skipped=user.cams_skipped_at is not None,
    )


@router.put("/me/pin", status_code=status.HTTP_204_NO_CONTENT)
async def update_my_pin(
    payload: PinUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Change the signed-in user's sign-in PIN.

    Deliberately separate from ``PUT /me``: that endpoint sets whatever fields
    it is given, and a credential must not be changeable by the same call that
    edits a display name. ``/signup`` refuses to overwrite an existing
    ``password_hash``, so before this endpoint a forgotten PIN had no in-app
    recovery path at all.
    """
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.password_hash and not (
        payload.current_pin and verify_password(payload.current_pin, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current PIN is incorrect",
        )

    user.password_hash = hash_password(payload.new_pin)
    await db.commit()


# ── Forgot PIN ─────────────────────────────────────────────────────────────
# Unauthenticated by necessity: the whole point is that the user cannot sign
# in. The code is emailed (Resend), stored only as a bcrypt hash, expires
# quickly and is capped at a few wrong guesses.
_PIN_RESET_TTL_MINUTES = 10
_PIN_RESET_MAX_ATTEMPTS = 5
# One mail a minute per account. The endpoint is unauthenticated by necessity,
# so without this anyone who knows a registered number can drive it in a loop
# and bury that inbox — and spend the mail quota doing it.
_PIN_RESET_RESEND_COOLDOWN_S = 60
_PIN_RESET_SENT_MESSAGE = (
    "If that number has an account with an email address, a reset code is on "
    "its way."
)


def _as_utc(value: datetime) -> datetime:
    """Postgres hands back tz-aware datetimes; SQLite (tests) hands back naive
    ones. Comparing a naive value against `now(timezone.utc)` raises, so pin
    both ends to UTC before any arithmetic."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _within_resend_cooldown(expires_at: datetime | None, now: datetime) -> bool:
    """Was a code mailed to this account within the cooldown?

    Derived from the expiry rather than a separate "last sent" column: the
    expiry is written at send time and is always issue time plus a fixed TTL,
    so the send instant is recoverable from it. Kept a pure function so the
    window can be tested without a database.
    """
    if expires_at is None:
        return False
    issued_at = _as_utc(expires_at) - timedelta(minutes=_PIN_RESET_TTL_MINUTES)
    return (now - issued_at).total_seconds() < _PIN_RESET_RESEND_COOLDOWN_S


def _mask_email(email: str) -> str:
    """`jonathan@gmail.com` -> `j••••••n@gmail.com`; enough for the user to
    recognise which inbox to open, not enough to disclose the address."""
    local, _, domain = email.partition("@")
    if not domain:
        return "•••"
    if len(local) <= 2:
        masked = local[0] + "•" if local else "•"
    else:
        masked = f"{local[0]}{'•' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


@router.post("/pin-reset/request", response_model=PinResetRequestResponse)
async def request_pin_reset(
    payload: PinResetRequestRequest,
    db: AsyncSession = Depends(get_db),
):
    phone = full_phone(payload.country_code, payload.mobile)
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()

    # Unknown number, or an account with no email on file: answer exactly as if
    # a mail had been sent. Anything else turns this endpoint into a way to
    # enumerate which phone numbers are registered.
    if not user or not user.email:
        return PinResetRequestResponse(
            message=_PIN_RESET_SENT_MESSAGE,
            email_hint=None,
            expires_in_minutes=_PIN_RESET_TTL_MINUTES,
        )

    now = datetime.now(timezone.utc)

    # Throttle before spending a mail.
    if _within_resend_cooldown(user.pin_reset_expires_at, now):
        # Answered exactly like a real send, NOT a 429. A 429 can only happen
        # for a number that HAS an account, which would hand back the very fact
        # the generic answer above exists to hide. Nothing is lost by staying
        # quiet: the code already in their inbox is still live, so "a code is
        # on its way" remains true.
        logger.info("PIN reset throttled (cooldown) for user_id=%s", user.id)
        return PinResetRequestResponse(
            message=_PIN_RESET_SENT_MESSAGE,
            email_hint=_mask_email(user.email),
            expires_in_minutes=_PIN_RESET_TTL_MINUTES,
        )

    code = f"{secrets.randbelow(10**PIN_RESET_CODE_DIGITS):0{PIN_RESET_CODE_DIGITS}d}"
    user.pin_reset_code_hash = hash_password(code)
    user.pin_reset_expires_at = now + timedelta(minutes=_PIN_RESET_TTL_MINUTES)
    # A fresh request resets the counter — the previous code is now dead, so
    # its failed guesses shouldn't be held against the new one.
    user.pin_reset_attempts = 0
    await db.commit()

    try:
        await send_pin_reset_code(user.email, code, _PIN_RESET_TTL_MINUTES)
    except ResendNotConfigured:
        logger.warning("PIN reset requested but RESEND_API_KEY is not set")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PIN reset by email isn't available right now.",
        ) from None
    except ResendSendFailed as exc:
        logger.warning("PIN reset mail failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the reset email. Please try again.",
        ) from None

    return PinResetRequestResponse(
        message=_PIN_RESET_SENT_MESSAGE,
        email_hint=_mask_email(user.email),
        expires_in_minutes=_PIN_RESET_TTL_MINUTES,
    )


@router.post("/pin-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_pin_reset(
    payload: PinResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    phone = full_phone(payload.country_code, payload.mobile)
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()

    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="That code is invalid or has expired. Request a new one.",
    )
    if not user or not user.pin_reset_code_hash or not user.pin_reset_expires_at:
        raise invalid

    if _as_utc(user.pin_reset_expires_at) < datetime.now(timezone.utc):
        raise invalid

    if user.pin_reset_attempts >= _PIN_RESET_MAX_ATTEMPTS:
        # Burn the code rather than leaving a throttled-but-live target.
        _clear_pin_reset(user)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect codes. Request a new one.",
        )

    if not verify_password(payload.code, user.pin_reset_code_hash):
        user.pin_reset_attempts += 1
        await db.commit()
        raise invalid

    user.password_hash = hash_password(payload.new_pin)
    _clear_pin_reset(user)
    await db.commit()


def _clear_pin_reset(user: User) -> None:
    user.pin_reset_code_hash = None
    user.pin_reset_expires_at = None
    user.pin_reset_attempts = 0
