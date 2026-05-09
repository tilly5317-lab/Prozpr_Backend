"""Rebalancing-side chart builder dispatcher.

Mirrors ``build_aa.py`` but for rebal-shape builders that take a
``RebalancingComputeResponse`` instead of ``(db, user_id)``. The chat-side
caller (``brain.py``'s rebalancing branch) hands us the engine response and
the chart names returned by the selector; we look each name up in the central
registry, call the builder with the response, and collect non-None payloads.

AA-shape builders (those expecting ``(db, user_id)``) are silently skipped
here — they belong to ``build_aa.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.visualization_tools.registry import CHART_TOOLS

logger = logging.getLogger(__name__)


async def build_charts_for_rebalancing(
    response: Any, chart_names: list[str]
) -> list[Any]:
    """Build rebalancing-flow chart payloads for the given names."""
    out: list[Any] = []
    for name in chart_names:
        tool = CHART_TOOLS.get(name)
        if tool is None:
            logger.info("build_rebalancing: unknown chart name %s skipped", name)
            continue
        try:
            payload = await tool.builder(response)
        except TypeError:
            # Wrong signature — this chart wants the AA-shape input
            # (``db, user_id``). Belongs to ``build_aa``; skip.
            logger.info("build_rebalancing: %s requires AA input; skipped", name)
            continue
        except Exception as exc:
            logger.warning("build_rebalancing: builder %s failed (%s); skipping", name, exc)
            continue
        if payload is not None:
            out.append(payload)
    return out
