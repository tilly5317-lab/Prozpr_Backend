"""Lightweight view over allocation subgroup JSON for rebalancing.

Used instead of the removed ``asset_allocation_pydantic`` allocation output model
for the narrow surface rebalancing needs (``aggregated_subgroups``).
"""

from __future__ import annotations

from typing import Any, Optional


class AggregatedSubgroupRow:
    __slots__ = ("subgroup", "total")

    def __init__(self, subgroup: str, total: float) -> None:
        self.subgroup = subgroup
        self.total = total


class CachedAssetAllocationView:
    """Minimal stand-in for parsed allocation JSON (subgroup targets)."""

    def __init__(self, document: dict[str, Any]) -> None:
        rows = document.get("aggregated_subgroups") or []
        parsed: list[AggregatedSubgroupRow] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            sg = str(r.get("subgroup") or r.get("asset_subgroup") or "")
            raw_total = r.get("total")
            try:
                total = float(raw_total) if raw_total is not None else 0.0
            except (TypeError, ValueError):
                total = 0.0
            parsed.append(AggregatedSubgroupRow(sg, total))
        self.aggregated_subgroups = parsed


def try_parse_asset_allocation_json(
    data: Any,
) -> Optional[CachedAssetAllocationView]:
    """Return a view instance or None if *data* is unusable."""
    if not isinstance(data, dict):
        return None
    try:
        return CachedAssetAllocationView(data)
    except Exception:
        return None
