"""Normalise engine payloads to the canonical inner allocation dict.

Accepted inputs:
  - The inner dict itself (has ``client_summary`` / ``bucket_allocations``).
  - A wrapper dict with ``allocation_output`` or ``goal_allocation_output``.
  - A Pydantic v2 model whose ``model_dump()`` yields either shape above.

No ``AI_Agents/`` imports — works purely on already-produced values.
"""

from __future__ import annotations

from typing import Any


def normalize_asset_allocation_engine_result(raw: Any) -> dict[str, Any]:
    """Return the canonical inner allocation document as a plain dict."""
    if raw is None:
        raise ValueError("allocation engine result is None")

    data = raw
    if hasattr(raw, "model_dump"):
        data = raw.model_dump(mode="python")
    if not isinstance(data, dict):
        raise TypeError(
            f"allocation engine result must be dict or pydantic model, got {type(raw)!r}"
        )

    inner = data.get("allocation_output") or data.get("goal_allocation_output")
    if isinstance(inner, dict):
        return inner

    if "client_summary" in data or "bucket_allocations" in data:
        return data

    raise ValueError(
        "allocation result missing allocation_output / goal_allocation_output "
        "or client_summary / bucket_allocations"
    )
