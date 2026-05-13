"""Insert the parent ``asset_allocation_runs`` row from a normalised doc."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_allocation.run import AssetAllocationRun, AssetAllocationRunStatus


def _float(value: Any, default: float = 0.0) -> float:
    """Safe float coercion; returns *default* on None or bad type."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_asset_allocation_run_row(
    *,
    doc: dict[str, Any],
    user_id: uuid.UUID,
    portfolio_id: uuid.UUID | None,
    chat_session_id: uuid.UUID | None,
    pipeline_source: str,
    spine_mode: str | None,
    user_question: str | None,
    input_payload: dict[str, Any],
) -> AssetAllocationRun:
    """Build an ORM row (not yet added to the session)."""
    summary = doc.get("client_summary") or {}
    breakdown = doc.get("asset_class_breakdown") or {}
    actual = breakdown.get("actual") or {}

    age = summary.get("age")
    if age is None:
        raise ValueError("client_summary.age is required to persist an asset allocation run")

    risk = summary.get("effective_risk_score")
    if risk is None:
        raise ValueError("client_summary.effective_risk_score is required")

    return AssetAllocationRun(
        user_id=user_id,
        portfolio_id=portfolio_id,
        chat_session_id=chat_session_id,
        status=AssetAllocationRunStatus.pending,
        pipeline_source=pipeline_source,
        spine_mode=spine_mode,
        user_question=user_question,
        rationale=doc.get("rationale"),
        input_payload=input_payload or {},
        client_age=int(age),
        client_occupation=(
            str(summary["occupation"])[:80] if summary.get("occupation") else None
        ),
        client_effective_risk_score=_float(risk),
        total_corpus=_float(summary.get("total_corpus")),
        grand_total=_float(doc.get("grand_total")),
        equity_total=_float(actual.get("equity_total")),
        debt_total=_float(actual.get("debt_total")),
        others_total=_float(actual.get("others_total")),
        equity_total_pct=_float(actual.get("equity_total_pct")),
        debt_total_pct=_float(actual.get("debt_total_pct")),
        others_total_pct=_float(actual.get("others_total_pct")),
        all_amounts_in_multiples_of_100=bool(
            doc.get("all_amounts_in_multiples_of_100", False)
        ),
    )


async def insert_asset_allocation_run(
    db: AsyncSession,
    *,
    doc: dict[str, Any],
    user_id: uuid.UUID,
    portfolio_id: uuid.UUID | None,
    chat_session_id: uuid.UUID | None,
    pipeline_source: str,
    spine_mode: str | None,
    user_question: str | None,
    input_payload: dict[str, Any],
) -> AssetAllocationRun:
    """Create + flush the ``asset_allocation_runs`` header row."""
    row = build_asset_allocation_run_row(
        doc=doc,
        user_id=user_id,
        portfolio_id=portfolio_id,
        chat_session_id=chat_session_id,
        pipeline_source=pipeline_source,
        spine_mode=spine_mode,
        user_question=user_question,
        input_payload=input_payload,
    )
    db.add(row)
    await db.flush()
    return row
