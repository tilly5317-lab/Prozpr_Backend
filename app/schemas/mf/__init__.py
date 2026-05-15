"""Pydantic schemas for mutual-fund domain CRUD APIs."""

from app.schemas.mf.aa_import import (
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
from app.schemas.mf.fund_metadata import (
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
from app.schemas.mf.holding_detail import (
    MfHoldingDetailResponse,
    MfHoldingNavPoint,
    MfHoldingPosition,
    MfHoldingTransactionItem,
)
from app.schemas.mf.latest_snapshot import UserMfLatestSnapshotResponse
from app.schemas.mf.nav_history import MfNavHistoryCreate, MfNavHistoryResponse, MfNavHistoryUpdate
from app.schemas.mf.portfolio_snapshot import (
    PortfolioAllocationSnapshotCreate,
    PortfolioAllocationSnapshotResponse,
    PortfolioAllocationSnapshotUpdate,
)
from app.schemas.mf.sip_mandate import MfSipMandateCreate, MfSipMandateResponse, MfSipMandateUpdate
from app.schemas.mf.transaction import MfTransactionCreate, MfTransactionResponse, MfTransactionUpdate
from app.schemas.mf.user_investment_list import (
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
