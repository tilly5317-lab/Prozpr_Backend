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
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext

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
    # Goal-tied bucket block derived from the AA output that drove this rebalance.
    # None when AA output wasn't available; consumed by the formatter facts pack.
    goal_buckets: Optional[list[dict[str, Any]]] = None


FUND_ACTIONS_LIMIT = 30


_BUCKET_HORIZON_LABELS = {
    "emergency": "Emergency reserve",
    "short_term": "Short-term (< 3 yrs)",
    "medium_term": "Medium-term (3-5 yrs)",
    "long_term": "Long-term (> 5 yrs)",
}


def build_goal_buckets_block(
    allocation_output: "GoalAllocationOutput",
) -> list[dict[str, Any]]:
    """Goal-tied bucket view derived from the AA output that drove this rebalance.

    Lets the formatter LLM tie trades back to the goals and equity/debt/others
    split that justified them. One entry per bucket the customer has goals in,
    plus the planned asset-class % split for that bucket.

    Shape (one entry per bucket):
      {
        "bucket": "long_term",
        "horizon_label": "Long-term (> 5 yrs)",
        "goals": [{
          "name": <str>,
          "horizon_months": <int>,
          "amount_needed_inr": <float>, "amount_needed_indian": <str>,
          "priority": "non_negotiable" | "negotiable",
        }, ...],
        "total_goal_amount_inr": <float>, "total_goal_amount_indian": <str>,
        "allocated_amount_inr":  <float>, "allocated_amount_indian":  <str>,
        "planned_split_pct": {"equity": <float>, "debt": <float>, "others": <float>},
      }
    """
    per_bucket_split = {
        bs.bucket: bs
        for bs in allocation_output.asset_class_breakdown.planned.per_bucket
    }
    out: list[dict[str, Any]] = []
    for bucket_alloc in allocation_output.bucket_allocations:
        if not bucket_alloc.goals and bucket_alloc.bucket != "emergency":
            # Skip empty non-emergency buckets — nothing meaningful to anchor.
            continue
        split = per_bucket_split.get(bucket_alloc.bucket)
        out.append({
            "bucket": bucket_alloc.bucket,
            "horizon_label": _BUCKET_HORIZON_LABELS.get(
                bucket_alloc.bucket, bucket_alloc.bucket,
            ),
            "goals": [
                {
                    "name": g.goal_name,
                    "horizon_months": g.time_to_goal_months,
                    "amount_needed_inr": float(g.amount_needed),
                    "amount_needed_indian": format_inr_indian(g.amount_needed),
                    "priority": g.goal_priority,
                }
                for g in bucket_alloc.goals
            ],
            "total_goal_amount_inr": float(bucket_alloc.total_goal_amount),
            "total_goal_amount_indian": format_inr_indian(
                bucket_alloc.total_goal_amount,
            ),
            "allocated_amount_inr": float(bucket_alloc.allocated_amount),
            "allocated_amount_indian": format_inr_indian(
                bucket_alloc.allocated_amount,
            ),
            "planned_split_pct": {
                "equity": float(split.equity_pct) if split else 0.0,
                "debt": float(split.debt_pct) if split else 0.0,
                "others": float(split.others_pct) if split else 0.0,
            },
        })
    return out


def build_rebal_facts_pack(
    response: "RebalancingComputeResponse",
    *,
    goal_buckets: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
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

        # Per-fund actions (top FUND_ACTIONS_LIMIT by exposure). Lets the LLM
        # narrate fund-specific questions ("why are you trimming HDFC Top 100?").
        # No ISINs — fund_name is customer-tellable, ISIN isn't.
        "fund_actions": [{
            "fund_name":          <str>,                                     # e.g. "HDFC Top 100"
            "sub_category":       <str>,                                     # SEBI category
            "asset_subgroup":     <str>,                                     # engine grouping (context only)
            "current_inr":        <float>, "current_indian":        <str>,
            "buy_inr":            <float>, "buy_indian":            <str>,
            "sell_inr":           <float>, "sell_indian":           <str>,
            "planned_final_inr":  <float>, "planned_final_indian":  <str>,
        }, ...],
        # Number of additional smaller holdings beyond fund_actions cap
        # (only present when truncated).
        "more_holdings_count": <int>,

        # Optional — present when AA output drove this rebalance. Lets the LLM
        # tie trades back to goals + horizon + planned equity/debt/others split.
        # See ``build_goal_buckets_block`` for shape.
        "goal_buckets": [...],
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

    # Aggregate per-fund actions into (asset_subgroup, sub_category) buckets,
    # and collect per-fund rows for fund_actions.
    # This mirrors formatter._bucketise so the LLM and the deterministic
    # fallback brief speak the same language. The customer-facing key is
    # ``sub_category`` (SEBI category like "Large Cap Fund") — never
    # ``asset_subgroup`` (internal engine grouping).
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    fund_rows: list[dict[str, Any]] = []
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

            fund_name = getattr(action, "recommended_fund", None)
            if fund_name:
                fund_rows.append({
                    "fund_name": fund_name,
                    "sub_category": sub_cat,
                    "asset_subgroup": sg_subgroup,
                    "current_inr": present,
                    "buy_inr": buy,
                    "sell_inr": sell,
                    "planned_final_inr": present + buy - sell,
                })

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

    # fund_actions: top FUND_ACTIONS_LIMIT by max(current, planned_final).
    # Aggregate any duplicate (fund_name, sub_category) rows from multiple
    # rank slots so the LLM sees one entry per actual fund.
    fund_by_name: dict[tuple[str, Any], dict[str, Any]] = {}
    for fr in fund_rows:
        key = (fr["fund_name"], fr["sub_category"])
        existing = fund_by_name.get(key)
        if existing is None:
            fund_by_name[key] = fr
        else:
            existing["current_inr"] += fr["current_inr"]
            existing["buy_inr"] += fr["buy_inr"]
            existing["sell_inr"] += fr["sell_inr"]
            existing["planned_final_inr"] += fr["planned_final_inr"]

    fund_actions_all = sorted(
        fund_by_name.values(),
        key=lambda f: -max(f["current_inr"], f["planned_final_inr"]),
    )
    fund_actions = fund_actions_all[:FUND_ACTIONS_LIMIT]
    for fa in fund_actions:
        fa["current_indian"] = format_inr_indian(fa["current_inr"])
        fa["buy_indian"] = format_inr_indian(fa["buy_inr"])
        fa["sell_indian"] = format_inr_indian(fa["sell_inr"])
        fa["planned_final_indian"] = format_inr_indian(fa["planned_final_inr"])
    more_holdings_count = max(0, len(fund_actions_all) - FUND_ACTIONS_LIMIT)

    pack: dict[str, Any] = {
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
        "fund_actions": fund_actions,
    }
    if more_holdings_count > 0:
        pack["more_holdings_count"] = more_holdings_count
    if goal_buckets:
        pack["goal_buckets"] = goal_buckets
    return pack


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
    chat_ctx: "TurnContext | None" = None,
) -> RebalancingRunOutcome:
    """Top-level orchestrator: cache → builder → engine → persist → format.

    When ``persist=False`` (counterfactual_explore path), the engine still
    runs and reads from the database (holdings, NAVs, metadata, cached
    allocation), but the recommendation row and the chat-ai-module-runs
    telemetry write are skipped. Returns the same outcome shape;
    ``recommendation_id`` is None.

    When ``force_fresh_allocation=True``, the AA cache lookup is skipped and
    AA is always re-run inline. Used when the chat layer has set chat_ctx
    overrides (e.g., ``additional_cash_inr``) that the cached AA result
    wouldn't reflect.
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

    if chat_ctx is None:
        from app.services.chat_core.turn_context import TurnContext  # lazy: avoids ai_bridge ↔ chat_core cycle at import time

        chat_ctx = TurnContext(
            user_ctx=user,
            user_question=user_question,
            conversation_history=[],
            client_context=None,
            session_id=chat_session_id or uuid.uuid4(),
            db=db,
            effective_user_id=acting_user_id,
            last_agent_runs={},
            active_intent=None,
            chat_overrides=None,
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
            chat_ctx=chat_ctx,
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
            chat_ctx, cached_output,
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

    # Goal-tied bucket block — derived once from the AA output that drove this
    # rebalance, persisted alongside the response so follow-up turns
    # (narrate / educate) see the same goal context.
    try:
        goal_buckets = build_goal_buckets_block(cached_output)
    except Exception as exc:
        logger.warning("goal_buckets_build_failed (non-fatal): %s", exc)
        goal_buckets = None

    rec_id: Optional[uuid.UUID] = None
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
                    "goal_buckets": goal_buckets,
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

    return RebalancingRunOutcome(
        response=response,
        formatted_text=formatted,
        recommendation_id=rec_id,
        allocation_snapshot_id=allocation_snapshot_id,
        source_allocation_id=source_allocation_id,
        used_cached_allocation=used_cache,
        goal_buckets=goal_buckets,
    )
