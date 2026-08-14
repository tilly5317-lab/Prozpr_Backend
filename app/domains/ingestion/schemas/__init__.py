"""Pydantic schema — `__init__.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""

from app.domains.ingestion.schemas.cams import (
    CamsCapabilitiesResponse,
    CamsPdfImportResponse,
    CamsStatementRequestBody,
    CamsStatementRequestResponse,
    CasDocumentDownloadResponse,
    CasDocumentItem,
    CasDocumentListResponse,
)
from app.domains.ingestion.schemas.finvu import (
    FinvuBucketInput,
    FinvuPortfolioSyncRequest,
    FinvuPortfolioSyncResponse,
)
from app.domains.ingestion.schemas.mf_aa import (
    MfAaNormalizeOneResponse,
    MfAaNormalizePendingRequest,
    MfAaNormalizePendingResponse,
)

__all__ = [
    "CamsCapabilitiesResponse",
    "CamsPdfImportResponse",
    "CamsStatementRequestBody",
    "CamsStatementRequestResponse",
    "CasDocumentDownloadResponse",
    "CasDocumentItem",
    "CasDocumentListResponse",
    "FinvuBucketInput",
    "FinvuPortfolioSyncRequest",
    "FinvuPortfolioSyncResponse",
    "MfAaNormalizeOneResponse",
    "MfAaNormalizePendingRequest",
    "MfAaNormalizePendingResponse",
]
