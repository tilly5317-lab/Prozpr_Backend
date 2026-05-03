"""AA-side chart builder dispatcher.

Given the list of chart names returned by the selector, call each registered
builder with the AA-shape signature ``(db, user_id)`` and collect the
non-None payloads. Unknown names and builders that produce ``None`` (no data,
no portfolio yet, etc.) are skipped silently — the chat answer renders without
that chart rather than failing.

Rebalancing-shape builders that take a ``RebalancingComputeResponse`` are NOT
dispatched here; they live in ``build_rebalancing.py`` (Plan 2). Names that
belong to the rebalancing flow are quietly skipped here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.visualization_tools.registry import CHART_TOOLS

logger = logging.getLogger(__name__)


async def build_charts_for_aa(
    db: AsyncSession, user_id: uuid.UUID, chart_names: list[str]
) -> list[Any]:
    """Build AA-flow chart payloads for the given names. Returns Pydantic
    payload instances; the caller dumps them via ``model_dump(mode='json')``.
    """
    out: list[Any] = []
    for name in chart_names:
        tool = CHART_TOOLS.get(name)
        if tool is None:
            logger.info("build_aa: unknown chart name %s skipped", name)
            continue
        try:
            payload = await tool.builder(db, user_id)
        except TypeError:
            # Wrong signature — this chart wants the rebalancing-shape input
            # (``response`` only). It belongs to ``build_rebalancing``;
            # silently skip in the AA dispatcher.
            logger.info("build_aa: %s requires rebalancing input; skipped", name)
            continue
        except Exception as exc:
            logger.warning("build_aa: builder %s failed (%s); skipping", name, exc)
            continue
        if payload is not None:
            out.append(payload)
    return out
