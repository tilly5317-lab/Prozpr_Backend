"""Persist goal-based allocation pipeline output across the normalized
``goal_allocation_*`` tables and emit an IDEAL ``PortfolioAllocationSnapshot``
for charts / detail views.

Returns ``(goal_allocation_run_id, portfolio_allocation_snapshot_id)``.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goals.goal_allocation_bucket import (
    AllocationBucketName,
    AssetClassSplitKind,
    GoalAllocationBucket,
    GoalAllocationBucketAssetClass,
    GoalAllocationBucketGoal,
    GoalAllocationBucketSubgroup,
)
from app.models.goals.goal_allocation_run import (
    GoalAllocationGoal,
    GoalAllocationRun,
    GoalAllocationRunStatus,
)
from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    AssetClassSplitBlock,
    BucketAllocation,
    GoalAllocationOutput,
    SubgroupBreakdown,
    SubgroupBucketSplit,
)


def _asset_class_split_block_for_bucket(
    block: AssetClassSplitBlock, bucket: str
) -> Optional[Any]:
    """Return the ``BucketAssetClassSplit`` for ``bucket`` from ``block``, or None."""
    for row in block.per_bucket:
        if row.bucket == bucket:
            return row
    return None


def _subgroups_for_bucket(
    breakdown: Optional[SubgroupBreakdown], kind: str, bucket: str
) -> dict[str, tuple[float, float]]:
    """Return ``{subgroup: (amount, pct_of_bucket)}`` for the chosen breakdown side."""
    if breakdown is None:
        return {}
    side: list[SubgroupBucketSplit] = (
        breakdown.planned if kind == "planned" else breakdown.actual
    )
    for split in side:
        if split.bucket == bucket:
            return {row.subgroup: (float(row.amount), float(row.pct_of_bucket)) for row in split.subgroups}
    return {}


async def persist_goal_allocation_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: GoalAllocationOutput,
    *,
    input_payload: dict[str, Any] | None = None,
    chat_session_id: uuid.UUID | None = None,
    user_question: str | None = None,
    spine_mode: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Write a ``GoalAllocationRun`` (+ children) and an IDEAL snapshot row."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)

    cs = output.client_summary
    acb = output.asset_class_breakdown
    actual_totals = acb.actual

    run = GoalAllocationRun(
        user_id=user_id,
        portfolio_id=portfolio.id,
        chat_session_id=chat_session_id,
        status=GoalAllocationRunStatus.pending,
        pipeline_source="asset_allocation_pydantic",
        spine_mode=spine_mode,
        user_question=user_question,
        input_payload=input_payload or {},
        client_age=cs.age,
        client_occupation=cs.occupation,
        client_effective_risk_score=float(cs.effective_risk_score),
        total_corpus=float(cs.total_corpus),
        grand_total=float(output.grand_total),
        equity_total=float(actual_totals.equity_total),
        debt_total=float(actual_totals.debt_total),
        others_total=float(actual_totals.others_total),
        equity_total_pct=float(actual_totals.equity_total_pct),
        debt_total_pct=float(actual_totals.debt_total_pct),
        others_total_pct=float(actual_totals.others_total_pct),
        all_amounts_in_multiples_of_100=output.all_amounts_in_multiples_of_100,
    )
    db.add(run)
    await db.flush()  # need run.id for children

    # Goal snapshots — keyed by name so bucket_goals can join back.
    goals_by_name: dict[str, GoalAllocationGoal] = {}
    for g in cs.goals:
        snap = GoalAllocationGoal(
            run_id=run.id,
            financial_goal_id=None,  # canonical id lost in pydantic conversion
            goal_name=g.goal_name,
            time_to_goal_months=g.time_to_goal_months,
            amount_needed=float(g.amount_needed),
            goal_priority=g.goal_priority,
            investment_goal=g.investment_goal,
        )
        db.add(snap)
        goals_by_name[g.goal_name] = snap
    await db.flush()

    # Buckets and their children.
    bucket: BucketAllocation
    for bucket in output.bucket_allocations:
        future_amount = (
            float(bucket.future_investment.future_investment_amount)
            if bucket.future_investment is not None
            else 0.0
        )
        future_msg = bucket.future_investment.message if bucket.future_investment else None

        bucket_row = GoalAllocationBucket(
            run_id=run.id,
            bucket_name=AllocationBucketName(bucket.bucket),
            total_goal_amount=float(bucket.total_goal_amount),
            allocated_amount=float(bucket.allocated_amount),
            rationale=bucket.rationale,
            future_investment_amount=future_amount,
            future_investment_message=future_msg,
        )
        db.add(bucket_row)
        await db.flush()  # need bucket_row.id for children

        # Goal-to-bucket join.
        for g in bucket.goals:
            snap = goals_by_name.get(g.goal_name)
            if snap is None:
                continue
            db.add(
                GoalAllocationBucketGoal(
                    bucket_id=bucket_row.id,
                    goal_id=snap.id,
                    goal_rationale=bucket.goal_rationales.get(g.goal_name),
                )
            )

        # Subgroup amounts — merge planned + actual into one row per subgroup.
        planned_subs = _subgroups_for_bucket(acb.subgroups, "planned", bucket.bucket)
        actual_subs = _subgroups_for_bucket(acb.subgroups, "actual", bucket.bucket)

        # Fall back to bucket.subgroup_amounts when the breakdown lacks subgroup detail.
        all_keys = set(planned_subs) | set(actual_subs) | set(bucket.subgroup_amounts.keys())
        for key in all_keys:
            planned_amt, planned_pct = planned_subs.get(key, (0.0, 0.0))
            actual_amt, actual_pct = actual_subs.get(key, (0.0, 0.0))
            if key not in planned_subs and key not in actual_subs:
                # Only the flat ``subgroup_amounts`` map has this — record as actual.
                actual_amt = float(bucket.subgroup_amounts[key])
            db.add(
                GoalAllocationBucketSubgroup(
                    bucket_id=bucket_row.id,
                    subgroup=key,
                    planned_amount=planned_amt,
                    actual_amount=actual_amt,
                    planned_pct_of_bucket=planned_pct,
                    actual_pct_of_bucket=actual_pct,
                )
            )

        # Asset-class split (planned + actual).
        planned_split = _asset_class_split_block_for_bucket(acb.planned, bucket.bucket)
        if planned_split is not None:
            db.add(
                GoalAllocationBucketAssetClass(
                    bucket_id=bucket_row.id,
                    split_kind=AssetClassSplitKind.planned,
                    equity_amount=float(planned_split.equity),
                    debt_amount=float(planned_split.debt),
                    others_amount=float(planned_split.others),
                    equity_pct=float(planned_split.equity_pct),
                    debt_pct=float(planned_split.debt_pct),
                    others_pct=float(planned_split.others_pct),
                )
            )
        actual_split = _asset_class_split_block_for_bucket(acb.actual, bucket.bucket)
        if actual_split is not None:
            db.add(
                GoalAllocationBucketAssetClass(
                    bucket_id=bucket_row.id,
                    split_kind=AssetClassSplitKind.actual,
                    equity_amount=float(actual_split.equity),
                    debt_amount=float(actual_split.debt),
                    others_amount=float(actual_split.others),
                    equity_pct=float(actual_split.equity_pct),
                    debt_pct=float(actual_split.debt_pct),
                    others_pct=float(actual_split.others_pct),
                )
            )

    # IDEAL snapshot — chart layer reads this for the donut.
    snapshot_allocation: dict[str, Any] = {
        "rows": [
            {"asset_class": "Equity", "weight_pct": float(actual_totals.equity_total_pct)},
            {"asset_class": "Debt", "weight_pct": float(actual_totals.debt_total_pct)},
            {"asset_class": "Others", "weight_pct": float(actual_totals.others_total_pct)},
        ],
        "equity_pct": float(actual_totals.equity_total_pct),
        "debt_pct": float(actual_totals.debt_total_pct),
        "others_pct": float(actual_totals.others_total_pct),
        "goal_allocation_output": output.model_dump(mode="json"),
        "goal_allocation_run_id": str(run.id),
    }
    snap = PortfolioAllocationSnapshot(
        user_id=user_id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation=snapshot_allocation,
        source=spine_mode or "asset_allocation_pydantic",
        notes=(user_question or "")[:2000] or None,
    )
    db.add(snap)

    await db.flush()
    return run.id, snap.id
