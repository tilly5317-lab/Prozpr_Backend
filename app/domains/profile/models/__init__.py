"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from app.domains.profile.models.asset_allocation_constraint import AssetAllocationConstraint
from app.domains.profile.models.effective_risk_assessment import EffectiveRiskAssessment
from app.domains.profile.models.investment_constraint import InvestmentConstraint
from app.domains.profile.models.investment_profile import InvestmentProfile
from app.domains.profile.models.other_investment import OtherInvestment, OtherInvestmentStatus
from app.domains.profile.models.review_preference import ReviewPreference
from app.domains.profile.models.risk_profile import RISK_CATEGORIES, RiskProfile
from app.domains.profile.models.tax_profile import TaxProfile
from app.domains.profile.models.personal_finance_profile import PersonalFinanceProfile
from app.domains.profile.models.user_current_property import UserCurrentProperty

__all__ = [
    "AssetAllocationConstraint",
    "EffectiveRiskAssessment",
    "InvestmentConstraint",
    "InvestmentProfile",
    "OtherInvestment",
    "OtherInvestmentStatus",
    "RISK_CATEGORIES",
    "ReviewPreference",
    "RiskProfile",
    "TaxProfile",
    "PersonalFinanceProfile",
    "UserCurrentProperty",
]
