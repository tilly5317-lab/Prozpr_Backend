"""Pydantic schema — `__init__.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from app.schemas.profile.constraints import (
    AllocationConstraintItem,
    InvestmentConstraintResponse,
    InvestmentConstraintUpdate,
)
from app.schemas.profile.effective_risk import (
    EffectiveRiskAssessmentResponse,
    EffectiveRiskRecalculateResponse,
)
from app.schemas.profile.full_profile import FullProfileResponse
from app.schemas.profile.investment import InvestmentProfileResponse, InvestmentProfileUpdate
from app.schemas.profile.personal import PersonalInfoResponse, PersonalInfoUpdate
from app.schemas.profile.review import ReviewPreferenceResponse, ReviewPreferenceUpdate
from app.schemas.profile.risk import RiskProfileResponse, RiskProfileUpdate
from app.schemas.profile.tax import TaxProfileResponse, TaxProfileUpdate

__all__ = [
    "AllocationConstraintItem",
    "EffectiveRiskAssessmentResponse",
    "EffectiveRiskRecalculateResponse",
    "FullProfileResponse",
    "InvestmentConstraintResponse",
    "InvestmentConstraintUpdate",
    "InvestmentProfileResponse",
    "InvestmentProfileUpdate",
    "PersonalInfoResponse",
    "PersonalInfoUpdate",
    "ReviewPreferenceResponse",
    "ReviewPreferenceUpdate",
    "RiskProfileResponse",
    "RiskProfileUpdate",
    "TaxProfileResponse",
    "TaxProfileUpdate",
]
