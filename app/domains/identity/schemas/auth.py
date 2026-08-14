"""Pydantic schema — `auth.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field, field_validator


def _normalize_country_code(value: str) -> str:
    v = value.strip()
    digits = "".join(c for c in v if c.isdigit())
    if not digits:
        return v
    return "+" + digits if not v.startswith("+") else "+" + digits


def _normalize_mobile(value: str) -> str:
    return "".join(c for c in value.strip() if c.isdigit())


# Accounts are keyed on a 10-digit national number. The country code is a
# SEPARATE field and must not be counted towards this length.
MOBILE_DIGITS = 10

# Length of the emailed forgot-PIN reset code. Six digits with a short expiry
# and a wrong-guess cap; see `auth_router` for the enforcement.
PIN_RESET_CODE_DIGITS = 6

# Formatting a user might paste or type. Anything else (letters in particular)
# is rejected outright rather than silently stripped — quietly dropping
# characters turns a typo into a different, valid-looking phone number.
_MOBILE_PUNCTUATION = frozenset(" -().+")


def _validate_mobile_digits(value: str) -> str:
    raw = value.strip()
    if any(not (c.isdigit() or c in _MOBILE_PUNCTUATION) for c in raw):
        raise ValueError("Mobile number must contain digits only")
    digits = _normalize_mobile(raw)
    if len(digits) != MOBILE_DIGITS:
        raise ValueError(
            f"Mobile number must be exactly {MOBILE_DIGITS} digits, "
            "excluding the country code"
        )
    return digits


def full_phone(country_code: str, mobile: str) -> str:
    cc = _normalize_country_code(country_code).lstrip("+")
    mob = _normalize_mobile(mobile)
    return "+" + cc + mob if cc else mob


class SignUpRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "country_code": "+91",
                    "mobile": "9876543210",
                    "password": "yourpassword8",
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                }
            ]
        }
    }

    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    password: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = None
    annual_income_min: float | None = None
    annual_income_max: float | None = None
    annual_expense_min: float | None = None
    annual_expense_max: float | None = None

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class SignUpResponse(BaseModel):
    user_id: uuid.UUID
    access_token: str
    token_type: str = "bearer"
    message: str = "Account created successfully"


class LoginRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "country_code": "+91",
                    "mobile": "9876543210",
                    "password": "yourpassword8",
                }
            ]
        }
    }

    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    password: str | None = Field(default=None, min_length=1)
    date_of_birth: date | None = None
    annual_income_min: float | None = None
    annual_income_max: float | None = None
    annual_expense_min: float | None = None
    annual_expense_max: float | None = None

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    country_code: str
    mobile: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_onboarding_complete: bool = False
    # True when the user chose "I'll do this later" on the onboarding CAMS step.
    # The app still offers the upload everywhere, but onboarding no longer
    # resumes onto that step. Reset automatically once a statement is imported.
    cams_skipped: bool = False


class UserUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class PinUpdateRequest(BaseModel):
    """Change the signed-in user's 4-digit sign-in PIN.

    ``current_pin`` is required whenever the account already has one — holding a
    valid session is not on its own enough to reset the credential that session
    was issued against. Accounts created before PINs existed (``password_hash``
    is NULL) can set one without it.
    """

    current_pin: str | None = Field(default=None, max_length=72)
    new_pin: str = Field(..., max_length=72)

    @field_validator("new_pin")
    @classmethod
    def validate_new_pin(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 4 or not v.isdigit():
            raise ValueError("PIN must be exactly 4 digits")
        return v


class PinResetRequestRequest(BaseModel):
    """Start a forgot-PIN reset for the account on this number."""

    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class PinResetRequestResponse(BaseModel):
    """Intentionally says the same thing whether or not the number has an
    account with an email — the response must not reveal who is registered.
    ``email_hint`` is a mask (``j••n@gmail.com``) and is null when nothing
    was sent, which the UI shows as generic guidance rather than an error."""

    message: str
    email_hint: str | None = None
    expires_in_minutes: int


class PinResetConfirmRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    code: str = Field(..., min_length=1, max_length=12)
    new_pin: str = Field(..., max_length=72)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        digits = "".join(c for c in v.strip() if c.isdigit())
        if len(digits) != PIN_RESET_CODE_DIGITS:
            raise ValueError(f"Code must be {PIN_RESET_CODE_DIGITS} digits")
        return digits

    @field_validator("new_pin")
    @classmethod
    def validate_new_pin(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 4 or not v.isdigit():
            raise ValueError("PIN must be exactly 4 digits")
        return v


class MobileLookupRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class MobileStatusResponse(BaseModel):
    exists: bool
    is_onboarding_complete: bool = False
    # Masked (`j••••••n@gmail.com`), never the address itself, so the reset
    # screen can name the inbox a code will go to before spending a mail. Only
    # ever set when `exists` is already True, which is what actually discloses
    # that the number is registered — see the router for the full reasoning.
    email_hint: str | None = None


# Backward-compatible alias
MobileExistsResponse = MobileStatusResponse


# ── OTP schemas ───────────────────────────────────────────


class SendOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class SendOtpResponse(BaseModel):
    message: str = "OTP sent successfully"
    type: str = "success"


class VerifyOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    otp: str = Field(..., min_length=4, max_length=9)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class VerifyOtpResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    is_new_user: bool = False


class ResendOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    retry_type: str = Field(default="text", pattern="^(text|voice)$")

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)


class WidgetVerifyRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=1, max_length=20)
    access_token: str = Field(..., min_length=1)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        return _validate_mobile_digits(v)
