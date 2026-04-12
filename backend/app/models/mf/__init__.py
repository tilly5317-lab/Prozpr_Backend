"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from app.models.mf.enums import (
    MfAaImportStatus,
    MfOptionType,
    MfPlanType,
    MfSipFrequency,
    MfSipStatus,
    MfStepupFrequency,
    MfTransactionSource,
    MfTransactionType,
    PortfolioSnapshotKind,
    UserInvestmentListKind,
)
from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.mf.mf_sip_mandate import MfSipMandate
from app.models.mf.mf_aa_import import MfAaImport, MfAaSummary, MfAaTransaction
from app.models.mf.mf_transaction import MfTransaction
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.models.mf.user_investment_list import UserInvestmentList

__all__ = [
    "MfAaImport",
    "MfAaImportStatus",
    "MfAaSummary",
    "MfAaTransaction",
    "MfFundMetadata",
    "MfNavHistory",
    "MfOptionType",
    "MfPlanType",
    "MfSipFrequency",
    "MfSipMandate",
    "MfSipStatus",
    "MfStepupFrequency",
    "MfTransaction",
    "MfTransactionSource",
    "MfTransactionType",
    "PortfolioAllocationSnapshot",
    "PortfolioSnapshotKind",
    "UserInvestmentList",
    "UserInvestmentListKind",
]
