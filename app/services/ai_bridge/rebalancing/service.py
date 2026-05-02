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
from app.services.ai_bridge.common import (
    asset_class_for_subgroup,
    ensure_ai_agents_path,
    format_inr_indian,
    trace_line,
)
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
        "total_portfolio_inr": <float>, "total_portfolio_indian": <str>,
        "buys_total_inr":      <float>, "buys_total_indian":      <str>,
        "sells_total_inr":     <float>, "sells_total_indian":     <str>,
        "tax_impact_inr":      <float>, "tax_impact_indian":      <str>,
        "trade_count":         int,

        # High-level asset-class summary, derived from per-bucket asset_subgroup.
        "asset_class_mix_pct":    {"equity": <float>, "debt": <float>, "others": <float>},
        "asset_class_mix_inr":    {"equity": <float>, "debt": <float>, "others": <float>},
        "asset_class_mix_indian": {"equity": <str>,   "debt": <str>,   "others": <str>},

        # Per (asset_subgroup, sub_category) bucket — sub_category is the
        # SEBI label (e.g., "Large Cap Fund") and is the customer-facing name.
        # asset_subgroup is internal engine context; do not surface it to the
        # customer.
        "buckets": [{
            "sub_category": <str>,                                       # e.g. "Large Cap Fund"
            "asset_subgroup": <str>,                                     # engine context
            "current_inr":        <float>, "current_indian":        <str>,
            "buy_inr":            <float>, "buy_indian":            <str>,
            "sell_inr":           <float>, "sell_indian":           <str>,
            "planned_final_inr":  <float>, "planned_final_indian":  <str>,
        }, ...],

        "warnings": [<short_string>, ...],   # human-readable, <= 5 entries
      }

    Money convention: every numeric ``*_inr`` field is paired with a sibling
    ``*_indian`` string pre-formatted in Indian notation. The chat formatter
    prompt instructs the LLM to copy ``*_indian`` verbatim and never compute
    its own lakh/crore conversion.

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

    # Aggregate per-fund actions into (asset_subgroup, sub_category) buckets.
    # This mirrors formatter._bucketise so the LLM and the deterministic
    # fallback brief speak the same language. The customer-facing key is
    # ``sub_category`` (SEBI category like "Large Cap Fund") — never
    # ``asset_subgroup`` (internal engine grouping).
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for sg in subgroups:
        sg_subgroup = getattr(sg, "asset_subgroup", None)
        for action in getattr(sg, "actions", []) or []:
            present = float(getattr(action, "present_allocation_inr", 0) or 0)
            buy = float(getattr(action, "pass1_buy_amount", 0) or 0)
            sell = float(
                (getattr(action, "pass1_sell_amount", 0) or 0)
                + (getattr(action, "pass2_sell_amount", 0) or 0)
            )
            # Skip phantom rows (no holding, no buy, no sell).
            if present <= 0 and buy <= 0 and sell <= 0:
                continue
            sub_cat = getattr(action, "sub_category", None)
            key = (sg_subgroup, sub_cat)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = {
                    "sub_category": sub_cat,
                    "asset_subgroup": sg_subgroup,
                    "current_inr": 0.0,
                    "buy_inr": 0.0,
                    "sell_inr": 0.0,
                }
                by_key[key] = bucket
            bucket["current_inr"] += present
            bucket["buy_inr"] += buy
            bucket["sell_inr"] += sell

    buckets: list[dict[str, Any]] = []
    for bucket in by_key.values():
        bucket["planned_final_inr"] = (
            bucket["current_inr"] + bucket["buy_inr"] - bucket["sell_inr"]
        )
        bucket["current_indian"] = format_inr_indian(bucket["current_inr"])
        bucket["buy_indian"] = format_inr_indian(bucket["buy_inr"])
        bucket["sell_indian"] = format_inr_indian(bucket["sell_inr"])
        bucket["planned_final_indian"] = format_inr_indian(bucket["planned_final_inr"])
        buckets.append(bucket)

    # High-level asset-class mix — group buckets by asset_subgroup → asset_class.
    asset_class_inr: dict[str, float] = {"equity": 0.0, "debt": 0.0, "others": 0.0}
    for b in buckets:
        cls = asset_class_for_subgroup(b.get("asset_subgroup"))
        asset_class_inr[cls] = asset_class_inr.get(cls, 0.0) + b["current_inr"]
    asset_class_total = sum(asset_class_inr.values()) or 0.0
    asset_class_pct = {
        cls: (amt / asset_class_total * 100 if asset_class_total > 0 else 0.0)
        for cls, amt in asset_class_inr.items()
    }
    asset_class_indian = {
        cls: format_inr_indian(amt) for cls, amt in asset_class_inr.items()
    }

    warnings: list[str] = []
    for w in warnings_list[:5]:
        msg = getattr(w, "message", None) or str(w)
        warnings.append(msg)

    return {
        "total_portfolio_inr": total_portfolio,
        "total_portfolio_indian": format_inr_indian(total_portfolio),
        "buys_total_inr": total_buy_inr,
        "buys_total_indian": format_inr_indian(total_buy_inr),
        "sells_total_inr": total_sell_inr,
        "sells_total_indian": format_inr_indian(total_sell_inr),
        "tax_impact_inr": tax_impact,
        "tax_impact_indian": format_inr_indian(tax_impact),
        "trade_count": sum(1 for r in rows if (
            float(getattr(r, "pass1_buy_amount", 0) or 0) > 0
            or float(getattr(r, "pass1_sell_amount", 0) or 0) > 0
        )),
        "asset_class_mix_pct": asset_class_pct,
        "asset_class_mix_inr": asset_class_inr,
        "asset_class_mix_indian": asset_class_indian,
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
    persist: bool = True,
    force_fresh_allocation: bool = False,
) -> RebalancingRunOutcome:
    """Top-level orchestrator: cache → builder → engine → persist → format.

    When ``persist=False`` (counterfactual_explore path), the engine still
    runs and reads from the database (holdings, NAVs, metadata, cached
    allocation), but the recommendation row, the chat-ai-module-runs
    telemetry write, and chart-picker call are skipped. Returns the same
    outcome shape; ``recommendation_id`` is None.

    When ``force_fresh_allocation=True``, the AA cache lookup is skipped and
    AA is always re-run inline. Used when the chat layer has set transient
    AA overrides (e.g., ``_chat_additional_cash_override``) that the cached
    AA result wouldn't reflect.
    """
    trace_line("module: rebalancing — start")

    if getattr(user, "date_of_birth", None) is None:
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_MISSING_DOB,
        )

    if not await _user_has_mf_holdings(db, acting_user_id):
        return RebalancingRunOutcome(
            response=None, blocking_message=_MSG_NO_HOLDINGS,
        )

    if force_fresh_allocation:
        # Counterfactual scenarios with AA-affecting overrides: skip cache.
        cached_output = None
        source_allocation_id: Optional[uuid.UUID] = None
        used_cache = False
    else:
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

    rec_id: Optional[uuid.UUID] = None
    chart = None
    if persist:
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

        # Pick a chart to surface alongside the brief. Picker is silent-fail —
        # if Haiku can't decide we still ship the first candidate, and if no
        # candidates apply (degenerate response) we ship no chart.
        candidates = available_charts(response)
        chart = await pick_chart(candidates, user_question)

    formatted = build_fallback_rebal_brief(
        response, used_cached_allocation=used_cache,
    )

    return RebalancingRunOutcome(
        response=response,
        formatted_text=formatted,
        recommendation_id=rec_id,
        allocation_snapshot_id=allocation_snapshot_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        chart=chart,
    )
