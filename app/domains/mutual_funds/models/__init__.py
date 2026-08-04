"""SQLAlchemy ORM model — `__init__.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""

from app.domains.mutual_funds.models.enums import (
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
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata
from app.domains.mutual_funds.models.mf_fund_rating import MfFundRating
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.models.mf_sip_mandate import MfSipMandate
from app.domains.mutual_funds.models.mf_aa_import import (
    MfAaImport,
    MfAaSummary,
    MfAaTransaction,
)
from app.domains.mutual_funds.models.mf_transaction import MfTransaction
from app.domains.mutual_funds.models.mf_allocation_snapshot import (
    PortfolioAllocationSnapshot,
)
from app.domains.mutual_funds.models.user_mf_latest_snapshot import UserMfLatestSnapshot
from app.domains.mutual_funds.models.user_cas_document import UserCasDocument
from app.domains.mutual_funds.models.user_investment_list import UserInvestmentList

__all__ = [
    "MfAaImport",
    "MfAaImportStatus",
    "UserCasDocument",
    "MfAaSummary",
    "MfAaTransaction",
    "MfFundMetadata",
    "MfFundRating",
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
    "UserMfLatestSnapshot",
    "PortfolioSnapshotKind",
    "UserInvestmentList",
    "UserInvestmentListKind",
]
