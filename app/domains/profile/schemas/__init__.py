from app.domains.profile.schemas.constraints import (
    AllocationConstraintItem,
    InvestmentConstraintResponse,
    InvestmentConstraintUpdate,
)
from app.domains.profile.schemas.effective_risk import (
    EffectiveRiskAssessmentResponse,
    EffectiveRiskRecalculateResponse,
)
from app.domains.profile.schemas.full_profile import FullProfileResponse
from app.domains.profile.schemas.investment_preferences import (
    InvestmentPreferenceIntent,
    InvestmentPreferencePreviewResponse,
    InvestmentPreferenceResponse,
)
from app.domains.profile.schemas.investment import (
    CurrentPropertiesUpdate,
    CurrentPropertyItem,
    CurrentPropertyResponse,
    InvestmentProfileResponse,
    InvestmentProfileUpdate,
)
from app.domains.profile.schemas.personal import (
    PersonalFinanceFields,
    PersonalFinanceResponse,
    PersonalFinanceUpdate,
    PersonalInfoResponse,
    PersonalInfoUpdate,
    PersonalProfileResponse,
    PersonalProfileUpdate,
)
from app.domains.profile.schemas.review import (
    ReviewPreferenceResponse,
    ReviewPreferenceUpdate,
)
from app.domains.profile.schemas.risk import (
    RiskProfileResponse,
    RiskProfileUpdate,
)
from app.domains.profile.schemas.tax import (
    TaxProfileResponse,
    TaxProfileUpdate,
)

__all__ = [
    "AllocationConstraintItem",
    "CurrentPropertiesUpdate",
    "CurrentPropertyItem",
    "CurrentPropertyResponse",
    "EffectiveRiskAssessmentResponse",
    "EffectiveRiskRecalculateResponse",
    "FullProfileResponse",
    "InvestmentConstraintResponse",
    "InvestmentConstraintUpdate",
    "InvestmentPreferenceIntent",
    "InvestmentPreferencePreviewResponse",
    "InvestmentPreferenceResponse",
    "InvestmentProfileResponse",
    "InvestmentProfileUpdate",
    "PersonalFinanceFields",
    "PersonalFinanceResponse",
    "PersonalFinanceUpdate",
    "PersonalInfoResponse",
    "PersonalInfoUpdate",
    "PersonalProfileResponse",
    "PersonalProfileUpdate",
    "ReviewPreferenceResponse",
    "ReviewPreferenceUpdate",
    "RiskProfileResponse",
    "RiskProfileUpdate",
    "TaxProfileResponse",
    "TaxProfileUpdate",
]
