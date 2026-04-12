"""Pydantic schema — `full_profile.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.profile.constraints import InvestmentConstraintResponse
from app.schemas.profile.investment import InvestmentProfileResponse
from app.schemas.profile.personal import PersonalInfoResponse
from app.schemas.profile.review import ReviewPreferenceResponse
from app.schemas.profile.risk import RiskProfileResponse
from app.schemas.profile.tax import TaxProfileResponse


class FullProfileResponse(BaseModel):
    personal_info: Optional[PersonalInfoResponse] = None
    investment_profile: Optional[InvestmentProfileResponse] = None
    risk_profile: Optional[RiskProfileResponse] = None
    investment_constraint: Optional[InvestmentConstraintResponse] = None
    tax_profile: Optional[TaxProfileResponse] = None
    review_preference: Optional[ReviewPreferenceResponse] = None
