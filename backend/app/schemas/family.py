"""Pydantic schema — `family.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import _normalize_country_code

VALID_RELATIONSHIPS = [
    "spouse", "child", "parent", "sibling", "grandparent", "grandchild", "other"
]
VALID_STATUSES = ["pending_otp", "active", "revoked"]


class AddFamilyMemberRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=120)
    email: Optional[str] = Field(default=None, max_length=320)
    phone: str = Field(..., min_length=6, max_length=32)
    country_code: str = Field(default="+91", min_length=1, max_length=10)
    relationship_type: str = Field(default="other", max_length=30)

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_RELATIONSHIPS:
            raise ValueError(f"Must be one of: {', '.join(VALID_RELATIONSHIPS)}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        normalized = _normalize_country_code(v)
        if not normalized or not normalized.lstrip("+").isdigit():
            raise ValueError("Invalid country code")
        return normalized


class OnboardFamilyMemberRequest(BaseModel):
    """Create a new user account for a family member who doesn't have one yet."""
    nickname: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=6, max_length=32)
    country_code: str = Field(default="+91", min_length=1, max_length=10)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=320)
    password: str = Field(..., min_length=8)
    relationship_type: str = Field(default="other", max_length=30)

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_RELATIONSHIPS:
            raise ValueError(f"Must be one of: {', '.join(VALID_RELATIONSHIPS)}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class VerifyFamilyOtpRequest(BaseModel):
    otp: str = Field(..., min_length=4, max_length=9)


class ResendFamilyOtpRequest(BaseModel):
    retry_type: str = Field(default="text", pattern="^(text|voice)$")


class OtpSentResponse(BaseModel):
    message: str = "OTP sent successfully"


class UpdateFamilyMemberRequest(BaseModel):
    nickname: Optional[str] = Field(default=None, max_length=120)
    relationship_type: Optional[str] = Field(default=None, max_length=30)

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in VALID_RELATIONSHIPS:
            raise ValueError(f"Must be one of: {', '.join(VALID_RELATIONSHIPS)}")
        return v


class FamilyMemberResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    owner_id: uuid.UUID
    member_user_id: Optional[uuid.UUID] = None
    nickname: str
    email: Optional[str] = None
    phone: Optional[str] = None
    relationship_type: str
    status: str
    member_first_name: Optional[str] = None
    member_last_name: Optional[str] = None
    member_initials: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FamilyMemberListResponse(BaseModel):
    members: list[FamilyMemberResponse]
    count: int


class FamilyMemberPortfolioSummary(BaseModel):
    member_id: uuid.UUID
    nickname: str
    relationship_type: str
    portfolio_value: float = 0
    total_invested: float = 0
    gain_percentage: Optional[float] = None


class CumulativeAllocationItem(BaseModel):
    asset_class: str
    total_amount: float = 0
    allocation_percentage: float = 0


class CumulativePortfolioResponse(BaseModel):
    total_value: float = 0
    total_invested: float = 0
    total_gain_percentage: Optional[float] = None
    member_count: int = 0
    members: list[FamilyMemberPortfolioSummary] = []
    combined_allocations: list[CumulativeAllocationItem] = []
