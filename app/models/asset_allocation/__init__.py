"""ORM models for persisted asset-allocation engine output.

PostgreSQL tables use the ``asset_allocation_*`` prefix (see ``app/models/asset_allocation/TABLES.md``).
"""

from app.models.asset_allocation.bucket import (
    AllocationBucketName,
    AssetAllocationBucket,
    AssetAllocationBucketAssetClass,
    AssetAllocationBucketRunTarget,
    AssetAllocationBucketSubgroup,
    AssetClassSplitKind,
)
from app.models.asset_allocation.run import (
    AssetAllocationRun,
    AssetAllocationRunStatus,
    AssetAllocationRunTarget,
)

__all__ = [
    "AllocationBucketName",
    "AssetAllocationBucket",
    "AssetAllocationBucketAssetClass",
    "AssetAllocationBucketRunTarget",
    "AssetAllocationBucketSubgroup",
    "AssetAllocationRun",
    "AssetAllocationRunStatus",
    "AssetAllocationRunTarget",
    "AssetClassSplitKind",
]
