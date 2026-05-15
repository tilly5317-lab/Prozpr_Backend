"""Orchestrate persistence of an engine run into the ``asset_allocation_*`` tables.

Call chain:
  1. Normalise the raw engine result → canonical inner dict.
  2. Resolve the portfolio id (explicit or primary fallback).
  3. Insert the run header row.
  4. Insert per-run target (goal) snapshots.
  5. Insert buckets + children (goal links, subgroups, asset-class splits).
  6. Insert aggregate rows (planned + actual equity/debt/others roll-up).

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
from app.services.ai_bridge.asset_allocation.persistence.write_aggregate import (
    insert_asset_allocation_aggregates,
)
from app.services.ai_bridge.asset_allocation.persistence.write_buckets import (
    insert_buckets_and_children,
)
from app.services.ai_bridge.common import trace_line

logger = logging.getLogger(__name__)


def _fmt_inr(v: float) -> str:
    """Compact INR format for trace logs."""
    if v >= 1_00_000:
        return f"₹{v / 1_00_000:.2f}L"
    return f"₹{v:,.0f}"


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

    trace_line("persist: ── asset_allocation DB writes ──")

    # 1. asset_allocation_runs
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
    summary = doc.get("client_summary") or {}
    breakdown = doc.get("asset_class_breakdown") or {}
    actual = breakdown.get("actual") or {}
    trace_line(
        f"persist: [asset_allocation_runs] run_id={run.id} "
        f"user_id={user_id} portfolio_id={pid} "
        f"status={run.status.value} spine_mode={spine_mode} "
        f"age={summary.get('age')} risk_score={summary.get('effective_risk_score')} "
        f"corpus={_fmt_inr(float(summary.get('total_corpus') or 0))} "
        f"grand_total={_fmt_inr(float(doc.get('grand_total') or 0))} "
        f"equity={_fmt_inr(float(actual.get('equity_total') or 0))}({actual.get('equity_total_pct', 0):.1f}%) "
        f"debt={_fmt_inr(float(actual.get('debt_total') or 0))}({actual.get('debt_total_pct', 0):.1f}%) "
        f"others={_fmt_inr(float(actual.get('others_total') or 0))}({actual.get('others_total_pct', 0):.1f}%)"
    )

    # 2. asset_allocation_run_targets
    target_map = await insert_asset_allocation_run_targets_for_run(
        db, run, doc, financial_goal_ids_by_name=financial_goal_ids_by_name,
    )
    goals = (doc.get("client_summary") or {}).get("goals") or []
    for g in goals:
        if isinstance(g, dict):
            gname = g.get("goal_name", "?")
            target_id = target_map.get(str(gname))
            trace_line(
                f"persist: [asset_allocation_run_targets] target_id={target_id} "
                f"goal={gname} months={g.get('time_to_goal_months')} "
                f"amount={_fmt_inr(float(g.get('amount_needed') or 0))} "
                f"priority={g.get('goal_priority')}"
            )

    # 3. asset_allocation_buckets + children (subgroups, asset_classes, goal links)
    buckets = doc.get("bucket_allocations") or []
    trace_line(f"persist: writing {len(buckets)} buckets → [asset_allocation_buckets] + children")
    for b in buckets:
        if isinstance(b, dict):
            bname = b.get("bucket", "?")
            trace_line(
                f"persist:   bucket={bname} "
                f"goal_amount={_fmt_inr(float(b.get('total_goal_amount') or 0))} "
                f"allocated={_fmt_inr(float(b.get('allocated_amount') or 0))} "
                f"goals={[g.get('goal_name') for g in (b.get('goals') or []) if isinstance(g, dict)]} "
                f"subgroups={list((b.get('subgroup_amounts') or {}).keys())}"
            )
    await insert_buckets_and_children(db, run, doc, target_map, user_id=user_id)

    # 4. asset_allocation_aggregates (planned + actual)
    await insert_asset_allocation_aggregates(
        db, run_id=run.id, user_id=user_id, doc=doc,
    )
    for key in ("planned", "actual"):
        blk = breakdown.get(key) or {}
        if blk:
            trace_line(
                f"persist: [asset_allocation_aggregates] {key}: "
                f"equity={_fmt_inr(float(blk.get('equity_total') or 0))} "
                f"debt={_fmt_inr(float(blk.get('debt_total') or 0))} "
                f"others={_fmt_inr(float(blk.get('others_total') or 0))}"
            )

    trace_line(
        f"persist: ── asset_allocation DB writes complete ── "
        f"run_id={run.id} tables=[asset_allocation_runs, "
        f"asset_allocation_run_targets({len(target_map)}), "
        f"asset_allocation_buckets({len(buckets)}), "
        f"bucket_run_targets, bucket_subgroups, bucket_asset_classes, aggregates]"
    )

    logger.info("saved asset allocation run_id=%s user_id=%s", run.id, user_id)
    return run.id
