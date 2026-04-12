"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from app.models.profile.asset_allocation_constraint import AssetAllocationConstraint
from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment
from app.models.profile.investment_constraint import InvestmentConstraint
from app.models.profile.investment_profile import InvestmentProfile
from app.models.profile.other_investment import OtherInvestment, OtherInvestmentStatus
from app.models.profile.review_preference import ReviewPreference
from app.models.profile.risk_profile import RISK_CATEGORIES, RiskProfile
from app.models.profile.tax_profile import TaxProfile
from app.models.profile.personal_finance_profile import PersonalFinanceProfile

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
]
