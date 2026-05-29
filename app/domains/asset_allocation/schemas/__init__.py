"""Asset-allocation schema package."""

from app.domains.asset_allocation.schemas.aggregate import AssetAllocationAggregateResponse
from app.domains.asset_allocation.schemas.bucket import AssetAllocationBucketResponse
from app.domains.asset_allocation.schemas.run import (
    AssetAllocationRunCreatedResponse,
    AssetAllocationRunDetailResponse,
    AssetAllocationRunRequest,
    AssetAllocationRunResponse,
)
from app.domains.asset_allocation.schemas.subgroup import AssetAllocationBucketSubgroupResponse

__all__ = [
    "AssetAllocationAggregateResponse",
    "AssetAllocationBucketResponse",
    "AssetAllocationBucketSubgroupResponse",
    "AssetAllocationRunCreatedResponse",
    "AssetAllocationRunDetailResponse",
    "AssetAllocationRunRequest",
    "AssetAllocationRunResponse",
]
