"""ORM models for persisted practical-allocation engine output.

PostgreSQL table: ``practical_asset_allocation_runs`` — one header row per
practical-allocation engine run, persisted by
``practical_allocation_persist_service``.
"""

from app.domains.practical_asset_allocation.models.run import (
    PracticalAssetAllocationRun,
)

__all__ = [
    "PracticalAssetAllocationRun",
]
