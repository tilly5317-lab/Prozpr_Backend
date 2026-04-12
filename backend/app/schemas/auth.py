"""Pydantic schema — `auth.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


def _normalize_country_code(value: str) -> str:
    v = value.strip()
    digits = "".join(c for c in v if c.isdigit())
    if not digits:
        return v
    return "+" + digits if not v.startswith("+") else "+" + digits


def _normalize_mobile(value: str) -> str:
    return "".join(c for c in value.strip() if c.isdigit())


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
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)

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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits

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
                {"country_code": "+91", "mobile": "9876543210", "password": "yourpassword8"}
            ]
        }
    }

    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str | None = Field(default=None, min_length=1)

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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits


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


# ── OTP schemas ───────────────────────────────────────────


class SendOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=6, max_length=20)

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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits


class SendOtpResponse(BaseModel):
    message: str = "OTP sent successfully"
    type: str = "success"


class VerifyOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=6, max_length=20)
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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits


class VerifyOtpResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    is_new_user: bool = False


class ResendOtpRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=6, max_length=20)
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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits


class WidgetVerifyRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=10)
    mobile: str = Field(..., min_length=6, max_length=20)
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
        digits = _normalize_mobile(v)
        if len(digits) < 6:
            raise ValueError("Mobile number too short")
        return digits
