"""Asset-allocation pipeline output models.

One ``AssetAllocationRun`` row is created per engine execution, with bucket,
subgroup, and aggregate child rows for traceability and downstream rebalancing.
"""

from app.models.asset_allocation.bucket import (
    AllocationBucketName,
    AssetAllocationBucket,
)
from app.models.asset_allocation.aggregate import (
    AssetAllocationAggregate,
    AssetClassSplitKind,
)
from app.models.asset_allocation.run import (
    AssetAllocationRun,
    AssetAllocationRunStatus,
)
from app.models.asset_allocation.subgroup import AssetAllocationBucketSubgroup

__all__ = [
    "AllocationBucketName",
    "AssetAllocationAggregate",
    "AssetAllocationBucket",
    "AssetAllocationBucketSubgroup",
    "AssetAllocationRun",
    "AssetAllocationRunStatus",
    "AssetClassSplitKind",
]
