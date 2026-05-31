"""SQL persistence for asset-allocation engine output (``asset_allocation_*`` tables)."""

from app.domains.asset_allocation.services.aa_engine.persistence.allocation_repository import (
    save_asset_allocation_from_engine_output,
)
from app.domains.asset_allocation.services.aa_engine.persistence.normalization import (
    normalize_asset_allocation_engine_result,
)

__all__ = [
    "normalize_asset_allocation_engine_result",
    "save_asset_allocation_from_engine_output",
]
