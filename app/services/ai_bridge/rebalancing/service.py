"""Cache-first rebalancing orchestrator.

Reads the most recent goal allocation for the user; if it's > 90 days old or
absent, re-runs allocation inline. Then materialises engine inputs, runs the
pipeline on a worker thread, persists the trade-list, and renders chat markdown.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rebalancing import RebalancingRecommendation, RecommendationType
from app.services.ai_bridge.asset_allocation.service import (
    AllocationRunOutcome,
    compute_allocation_result,
)
from app.services.ai_bridge.common import ensure_ai_agents_path, trace_line
from app.services.ai_bridge.rebalancing.chart_picker import pick_chart
from app.services.ai_bridge.rebalancing.charts import ChartSpec, available_charts
from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief
from app.services.ai_bridge.rebalancing.input_builder import (
    build_rebalancing_input_for_user,
)
from app.services.ai_module_telemetry import record_ai_module_run
from app.services.portfolio_service import get_or_create_primary_portfolio
from app.services.rebalancing_recommendation_persist import (
    persist_rebalancing_recommendation,
)

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    GoalAllocationOutput,
)
from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    RebalancingComputeResponse,
)
from Rebalancing.pipeline import run_rebalancing  # type: ignore[import-not-found]  # noqa: E402


logger = logging.getLogger(__name__)

ALLOCATION_TTL_DAYS = 90


_MSG_MISSING_DOB = (
    "I need your date of birth to plan trades — it anchors your tax aging "
    "and risk profile. Add it on your profile and ask me again."
)
_MSG_NO_HOLDINGS = "Connect your mutual fund portfolio and ask me again."
_MSG_ENGINE_ERROR = (
    "I couldn't compute your rebalancing plan right now. Try again in a moment, "
    "and if it keeps happening let us know via the help option."
)
_MSG_UNPRICEABLE = (
    "I couldn't price one of the recommended funds — looks like our market data "
    "is missing for it. Try again later or let us know via help."
)


@dataclass(frozen=True)
class RebalancingRunOutcome:
    response: Optional[RebalancingComputeResponse]
    formatted_text: Optional[str] = None
    blocking_message: Optional[str] = None
    recommendation_id: Optional[uuid.UUID] = None
    allocation_snapshot_id: Optional[uuid.UUID] = None
    source_allocation_id: Optional[uuid.UUID] = None
    used_cached_allocation: bool = False
    chart: Optional[ChartSpec] = None


def build_rebal_facts_pack(response: "RebalancingComputeResponse") -> dict[str, Any]:
    """Curated facts the LLM may cite. Customer-tellable only — no ISIN.

    Shape:
      {
        "total_portfolio_inr": <float>,
        "buys_total_inr": <float>,
        "sells_total_inr": <float>,
        "tax_impact_inr": <float>,
        "trade_count": int,
        "buckets": [{"asset_subgroup": str, "goal_target_inr": float,
                     "current_holding_inr": float, "suggested_final_inr": float,
                     "rebalance_inr": float}, ...],
        "warnings": [<short_string>, ...],   # human-readable, <= 5 entries
      }

    Fields are derived from ``response``; absent fields become 0/empty list.
    """
    rows = list(getattr(response, "rows", []) or [])
    warnings_list = list(getattr(response, "warnings", []) or [])

    buys_total = sum(
        float(getattr(r, "pass1_buy_amount", 0) or 0)
        for r in rows
    )
    sells_total = sum(
        float(getattr(r, "pass1_sell_amount", 0) or 0)
        for r in rows
    )

    # totals is a RebalancingTotals object; fall back to computed if absent
    totals_obj = getattr(response, "totals", None)
    tax_impact = float(
        getattr(totals_obj, "total_tax_estimate_inr", 0) or 0
    )
    total_buy_inr = float(getattr(totals_obj, "total_buy_inr", buys_total) or buys_total)
    total_sell_inr = float(getattr(totals_obj, "total_sell_inr", sells_total) or sells_total)

    # Derive portfolio total from subgroup current holdings (not gross trade volume).
    subgroups = list(getattr(response, "subgroups", []) or [])
    total_portfolio = sum(
        float(getattr(sg, "current_holding_inr", 0) or 0) for sg in subgroups
    )

    buckets: list[dict[str, Any]] = []
    for subgroup in subgroups:
        buckets.append({
            "asset_subgroup": getattr(subgroup, "asset_subgroup", None),
            "goal_target_inr": float(getattr(subgroup, "goal_target_inr", 0) or 0),
            "current_holding_inr": float(getattr(subgroup, "current_holding_inr", 0) or 0),
            "suggested_final_inr": float(
                getattr(subgroup, "suggested_final_holding_inr", 0) or 0
            ),
            "rebalance_inr": float(getattr(subgroup, "rebalance_inr", 0) or 0),
        })

    warnings: list[str] = []
    for w in warnings_list[:5]:
        msg = getattr(w, "message", None) or str(w)
        warnings.append(msg)

    return {
        "total_portfolio_inr": total_portfolio,
        "buys_total_inr": total_buy_inr,
        "sells_total_inr": total_sell_inr,
        "tax_impact_inr": tax_impact,
        "trade_count": sum(1 for r in rows if (
            float(getattr(r, "pass1_buy_amount", 0) or 0) > 0
            or float(getattr(r, "pass1_sell_amount", 0) or 0) > 0
        )),
        "buckets": buckets,
        "warnings": warnings,
    }


async def _user_has_mf_holdings(db: AsyncSession, user_id: uuid.UUID) -> bool:
    from app.models.mf.mf_transaction import MfTransaction

    row = (await db.execute(
        select(MfTransaction.id).where(MfTransaction.user_id == user_id).limit(1)
    )).first()
    return row is not None


async def _load_cached_allocation(
    db: AsyncSession, user_id: uuid.UUID,
) -> tuple[Optional[GoalAllocationOutput], Optional[uuid.UUID]]:
    """Latest ALLOCATION row ≤ 90 days old → (parsed output, row_id) or (None, None)."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ALLOCATION_TTL_DAYS)

    rec = (await db.execute(
        select(RebalancingRecommendation)
        .where(RebalancingRecommendation.portfolio_id == portfolio.id)
        .where(
            RebalancingRecommendation.recommendation_type
            == RecommendationType.ALLOCATION
        )
        .where(RebalancingRecommendation.created_at >= cutoff)
        .order_by(desc(RebalancingRecommendation.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if rec is None:
        return None, None
    payload = (rec.recommendation_data or {}).get("goal_allocation_output")
    if not payload:
        return None, None
    try:
        return GoalAllocationOutput.model_validate(payload), rec.id
    except Exception as exc:
        logger.warning("Cached allocation parse failed (%s); ignoring cache", exc)
        return None, None


async def compute_rebalancing_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
) -> RebalancingRunOutcome:
    """Top-level orchestrator: cache → builder → engine → persist → format."""
    trace_line("module: rebalancing — start")

    if getattr(user, "date_of_birth", None) is None:
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_MISSING_DOB,
        )

    if not await _user_has_mf_holdings(db, acting_user_id):
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_NO_HOLDINGS,
        )

    cached_output, source_allocation_id = await _load_cached_allocation(
        db, acting_user_id,
    )
    used_cache = cached_output is not None
    allocation_snapshot_id: Optional[uuid.UUID] = None

    if cached_output is None:
        trace_line(
            "rebalancing: allocation cache miss/stale — running allocation inline",
        )
        alloc_outcome: AllocationRunOutcome = await compute_allocation_result(
            user,
            user_question,
            db=db,
            persist_recommendation=True,
            acting_user_id=acting_user_id,
            chat_session_id=chat_session_id,
            spine_mode="rebalance_chained",
        )
        if alloc_outcome.blocking_message is not None:
            return RebalancingRunOutcome(
                response=None,
                blocking_message=alloc_outcome.blocking_message,
            )
        if alloc_outcome.result is None:
            return RebalancingRunOutcome(
                response=None, blocking_message=_MSG_ENGINE_ERROR,
            )
        cached_output = alloc_outcome.result
        source_allocation_id = alloc_outcome.rebalancing_recommendation_id
        allocation_snapshot_id = alloc_outcome.allocation_snapshot_id

    try:
        request, debug = await build_rebalancing_input_for_user(
            user, cached_output, db,
        )
    except Exception as exc:
        logger.exception("rebalancing input builder failed: %s", exc)
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_UNPRICEABLE,
        )

    trace_line(f"rebalancing input debug: {debug}")

    try:
        response: RebalancingComputeResponse = await asyncio.to_thread(
            run_rebalancing, request,
        )
    except Exception as exc:
        logger.exception("run_rebalancing failed: %s", exc)
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_ENGINE_ERROR,
        )

    rec_id = await persist_rebalancing_recommendation(
        db,
        acting_user_id,
        response,
        chat_session_id=chat_session_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        user_question=user_question,
    )

    try:
        await record_ai_module_run(
            db,
            user_id=acting_user_id,
            session_id=chat_session_id,
            module="rebalancing",
            reason="full_pipeline_run",
            intent_detected="rebalancing",
            spine_mode=None,
            input_payload=request.model_dump(mode="json"),
            output_payload={
                "rebalancing_response": response.model_dump(mode="json"),
                "correlation_ids": {
                    "recommendation_id": str(rec_id),
                    "source_allocation_id": (
                        str(source_allocation_id) if source_allocation_id else None
                    ),
                },
            },
            emit_standard_log=False,
        )
    except Exception as exc:
        logger.warning("ai_module_telemetry skipped (non-fatal): %s", exc)

    formatted = build_fallback_rebal_brief(
        response, used_cached_allocation=used_cache,
    )

    # Pick a chart to surface alongside the brief. Picker is silent-fail —
    # if Haiku can't decide we still ship the first candidate, and if no
    # candidates apply (degenerate response) we ship no chart.
    candidates = available_charts(response)
    chart = await pick_chart(candidates, user_question)

    return RebalancingRunOutcome(
        response=response,
        formatted_text=formatted,
        recommendation_id=rec_id,
        allocation_snapshot_id=allocation_snapshot_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        chart=chart,
    )
