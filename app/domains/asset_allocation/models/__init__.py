"""ORM models for persisted asset-allocation engine output.

PostgreSQL tables use the ``asset_allocation_*`` prefix (see ``app/models/asset_allocation/TABLES.md``).
"""

from app.domains.asset_allocation.models.bucket import (
    AllocationBucketName,
    AssetAllocationAggregate,
    AssetAllocationBucket,
    AssetAllocationBucketAssetClass,
    AssetAllocationBucketRunTarget,
    AssetAllocationBucketSubgroup,
    AssetClassSplitKind,
)
from app.domains.asset_allocation.models.run import (
    AssetAllocationRun,
    AssetAllocationRunStatus,
    AssetAllocationRunTarget,
)

__all__ = [
    "AllocationBucketName",
    "AssetAllocationAggregate",
    "AssetAllocationBucket",
    "AssetAllocationBucketAssetClass",
    "AssetAllocationBucketRunTarget",
    "AssetAllocationBucketSubgroup",
    "AssetAllocationRun",
    "AssetAllocationRunStatus",
    "AssetAllocationRunTarget",
    "AssetClassSplitKind",
]
