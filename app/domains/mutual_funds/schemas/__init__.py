"""Pydantic schemas for mutual-fund domain CRUD APIs."""

from app.domains.mutual_funds.schemas.aa_import import (
    MfAaImportCreate,
    MfAaImportResponse,
    MfAaImportUpdate,
    MfAaSummaryCreate,
    MfAaSummaryResponse,
    MfAaSummaryUpdate,
    MfAaTransactionCreate,
    MfAaTransactionResponse,
    MfAaTransactionUpdate,
)
from app.domains.mutual_funds.schemas.fund_metadata import (
    MfFundInvestorDetailResponse,
    MfFundMetadataCreate,
    MfFundMetadataListItem,
    MfFundMetadataResponse,
    MfFundMetadataSearchResponse,
    MfFundMetadataUpdate,
    MfFundRatingCreate,
    MfFundRatingResponse,
    MfFundRatingUpdate,
    MfNavChartPoint,
    MfNavDerivedReturns,
)
from app.domains.mutual_funds.schemas.holding_detail import (
    MfHoldingDetailResponse,
    MfHoldingNavPoint,
    MfHoldingPosition,
    MfHoldingTransactionItem,
)
from app.domains.mutual_funds.schemas.latest_snapshot import (
    UserMfLatestSnapshotResponse,
)
from app.domains.mutual_funds.schemas.nav_history import (
    MfNavHistoryCreate,
    MfNavHistoryResponse,
    MfNavHistoryUpdate,
)
from app.domains.mutual_funds.schemas.index_tri import (
    IndexTriHistoryCreate,
    IndexTriHistoryResponse,
)
from app.domains.mutual_funds.schemas.portfolio_snapshot import (
    PortfolioAllocationSnapshotCreate,
    PortfolioAllocationSnapshotResponse,
    PortfolioAllocationSnapshotUpdate,
)
from app.domains.mutual_funds.schemas.sip_mandate import (
    MfSipMandateCreate,
    MfSipMandateResponse,
    MfSipMandateUpdate,
)
from app.domains.mutual_funds.schemas.transaction import (
    MfTransactionCreate,
    MfTransactionResponse,
    MfTransactionUpdate,
)
from app.domains.mutual_funds.schemas.user_investment_list import (
    UserInvestmentListCreate,
    UserInvestmentListResponse,
    UserInvestmentListUpdate,
)

__all__ = [
    "MfAaImportCreate",
    "MfAaImportResponse",
    "MfAaImportUpdate",
    "MfAaSummaryCreate",
    "MfAaSummaryResponse",
    "MfAaSummaryUpdate",
    "MfAaTransactionCreate",
    "MfAaTransactionResponse",
    "MfAaTransactionUpdate",
    "MfFundInvestorDetailResponse",
    "MfFundMetadataCreate",
    "MfFundMetadataListItem",
    "MfFundMetadataResponse",
    "MfFundMetadataSearchResponse",
    "MfFundMetadataUpdate",
    "MfFundRatingCreate",
    "MfFundRatingResponse",
    "MfFundRatingUpdate",
    "MfHoldingDetailResponse",
    "MfHoldingNavPoint",
    "MfHoldingPosition",
    "MfHoldingTransactionItem",
    "UserMfLatestSnapshotResponse",
    "MfNavChartPoint",
    "IndexTriHistoryCreate",
    "IndexTriHistoryResponse",
    "MfNavDerivedReturns",
    "MfNavHistoryCreate",
    "MfNavHistoryResponse",
    "MfNavHistoryUpdate",
    "MfSipMandateCreate",
    "MfSipMandateResponse",
    "MfSipMandateUpdate",
    "MfTransactionCreate",
    "MfTransactionResponse",
    "MfTransactionUpdate",
    "PortfolioAllocationSnapshotCreate",
    "PortfolioAllocationSnapshotResponse",
    "PortfolioAllocationSnapshotUpdate",
    "UserInvestmentListCreate",
    "UserInvestmentListResponse",
    "UserInvestmentListUpdate",
]
