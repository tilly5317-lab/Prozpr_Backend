"""Pydantic schema — `full_profile.py`."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.domains.profile.schemas.constraints import InvestmentConstraintResponse
from app.domains.profile.schemas.investment import InvestmentProfileResponse
from app.domains.profile.schemas.personal import PersonalFinanceResponse, PersonalInfoResponse
from app.domains.profile.schemas.review import ReviewPreferenceResponse
from app.domains.profile.schemas.risk import RiskProfileResponse
from app.domains.profile.schemas.tax import TaxProfileResponse


class FullProfileResponse(BaseModel):
    personal_info: Optional[PersonalInfoResponse] = None
    personal_finance: Optional[PersonalFinanceResponse] = None
    investment_profile: Optional[InvestmentProfileResponse] = None
    risk_profile: Optional[RiskProfileResponse] = None
    investment_constraint: Optional[InvestmentConstraintResponse] = None
    tax_profile: Optional[TaxProfileResponse] = None
    review_preference: Optional[ReviewPreferenceResponse] = None
