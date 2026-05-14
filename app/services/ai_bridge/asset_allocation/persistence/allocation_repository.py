"""Orchestrate persistence of an engine run into the ``asset_allocation_*`` tables.

Call chain:
  1. Normalise the raw engine result → canonical inner dict.
  2. Resolve the portfolio id (explicit or primary fallback).
  3. Insert the run header row.
  4. Insert per-run target (goal) snapshots.
  5. Insert buckets + children (goal links, subgroups, asset-class splits).

The caller owns the transaction — ``commit`` / ``rollback`` is not called here.
Data-flow diagram: ``DATA_FLOW.md`` in this package.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import Portfolio
from app.services.ai_bridge.asset_allocation.persistence.normalization import (
    normalize_asset_allocation_engine_result,
)
from app.services.ai_bridge.asset_allocation.persistence.write_asset_allocation_run import (
    insert_asset_allocation_run,
)
from app.services.ai_bridge.asset_allocation.persistence.write_asset_allocation_run_targets import (
    insert_asset_allocation_run_targets_for_run,
)
from app.services.ai_bridge.asset_allocation.persistence.write_buckets import (
    insert_buckets_and_children,
)

logger = logging.getLogger(__name__)


# ── Portfolio resolution ────────────────────────────────────────────────


async def _resolve_portfolio_id(
    db: AsyncSession,
    user_id: uuid.UUID,
    explicit: uuid.UUID | None,
) -> uuid.UUID | None:
    """Return *explicit* if given, else the user's primary (or first) portfolio."""
    if explicit is not None:
        return explicit

    row = (
        await db.execute(
            select(Portfolio.id)
            .where(Portfolio.user_id == user_id, Portfolio.is_primary.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    return (
        await db.execute(
            select(Portfolio.id).where(Portfolio.user_id == user_id).limit(1)
        )
    ).scalar_one_or_none()


# ── Public entry point ──────────────────────────────────────────────────


async def save_asset_allocation_from_engine_output(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    portfolio_id: uuid.UUID | None,
    chat_session_id: uuid.UUID | None,
    pipeline_source: str,
    spine_mode: str | None,
    user_question: str | None,
    input_payload: dict[str, Any],
    engine_result: Any,
    financial_goal_ids_by_name: dict[str, uuid.UUID] | None = None,
) -> uuid.UUID:
    """Persist one engine run. Returns the new ``asset_allocation_runs.id``.

    ``engine_result`` may be the inner document, a wrapper dict, or a Pydantic
    model — ``normalize_asset_allocation_engine_result`` handles all shapes.
    """
    doc = normalize_asset_allocation_engine_result(engine_result)
    pid = await _resolve_portfolio_id(db, user_id, portfolio_id)

    run = await insert_asset_allocation_run(
        db,
        doc=doc,
        user_id=user_id,
        portfolio_id=pid,
        chat_session_id=chat_session_id,
        pipeline_source=pipeline_source,
        spine_mode=spine_mode,
        user_question=user_question,
        input_payload=input_payload,
    )

    target_map = await insert_asset_allocation_run_targets_for_run(
        db, run, doc, financial_goal_ids_by_name=financial_goal_ids_by_name,
    )

    await insert_buckets_and_children(db, run, doc, target_map)

    logger.info("saved asset allocation run_id=%s user_id=%s", run.id, user_id)
    return run.id
