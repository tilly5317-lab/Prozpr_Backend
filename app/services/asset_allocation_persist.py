"""Store the output of the asset-allocation pipeline.

This module is intentionally *narrow*: it takes the ``GoalAllocationOutput``
produced by ``AI_Agents/src/asset_allocation_pydantic`` and writes it into the
four normalized ``asset_allocation_*`` tables. Nothing else — no portfolio
lookup, no ``PortfolioAllocationSnapshot``, no recommendation rows, no chat
formatting. Callers that need those wire them up themselves.

Rows written per pipeline run (see ``docs/db_schema_asset_allocation.md``):

* 1 ``asset_allocation_runs`` row — audit: inputs, totals, rationale.
* ≤ 4 ``asset_allocation_buckets`` rows — one per time-horizon bucket.
* n ``asset_allocation_bucket_subgroups`` rows — planned + actual amounts per
  ``(bucket, subgroup)``.
* 2 ``asset_allocation_aggregate`` rows — run-level equity / debt / others
  roll-up, one ``planned`` and one ``actual``.

The session is *flushed* but not committed — the caller owns the transaction.
Returns the new ``asset_allocation_runs.id``.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_allocation import (
    AllocationBucketName,
    AssetAllocationAggregate,
    AssetAllocationBucket,
    AssetAllocationBucketSubgroup,
    AssetAllocationRun,
    AssetAllocationRunStatus,
    AssetClassSplitKind,
)
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    AssetClassSplitBlock,
    BucketAllocation,
    GoalAllocationOutput,
    SubgroupBreakdown,
    SubgroupBucketSplit,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
            return {
                row.subgroup: (float(row.amount), float(row.pct_of_bucket))
                for row in split.subgroups
            }
    return {}


def _aggregate_row(
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: AssetClassSplitKind,
    block: AssetClassSplitBlock,
) -> AssetAllocationAggregate:
    """Build a run-level equity / debt / others roll-up row from a split block."""
    return AssetAllocationAggregate(
        run_id=run_id,
        user_id=user_id,
        split_kind=kind,
        equity_amount=float(block.equity_total),
        debt_amount=float(block.debt_total),
        others_amount=float(block.others_total),
        equity_pct=float(block.equity_total_pct),
        debt_pct=float(block.debt_total_pct),
        others_pct=float(block.others_total_pct),
    )


def _bucket_subgroup_rows(
    bucket: BucketAllocation,
    bucket_id: uuid.UUID,
    user_id: uuid.UUID,
    breakdown: Optional[SubgroupBreakdown],
) -> list[AssetAllocationBucketSubgroup]:
    """One ``asset_allocation_bucket_subgroups`` row per subgroup in *bucket*.

    Merges the planned/actual breakdown (when present) and falls back to the
    flat ``subgroup_amounts`` map for any subgroup the breakdown omits.
    """
    planned_subs = _subgroups_for_bucket(breakdown, "planned", bucket.bucket)
    actual_subs = _subgroups_for_bucket(breakdown, "actual", bucket.bucket)

    rows: list[AssetAllocationBucketSubgroup] = []
    for key in set(planned_subs) | set(actual_subs) | set(bucket.subgroup_amounts):
        planned_amt, planned_pct = planned_subs.get(key, (0.0, 0.0))
        actual_amt, actual_pct = actual_subs.get(key, (0.0, 0.0))
        if key not in planned_subs and key not in actual_subs:
            actual_amt = float(bucket.subgroup_amounts[key])
        rows.append(
            AssetAllocationBucketSubgroup(
                bucket_id=bucket_id,
                user_id=user_id,
                subgroup=key,
                planned_amount=planned_amt,
                actual_amount=actual_amt,
                planned_pct_of_bucket=planned_pct,
                actual_pct_of_bucket=actual_pct,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def persist_asset_allocation_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    output: GoalAllocationOutput,
    portfolio_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    supersedes_id: uuid.UUID | None = None,
    user_question: str | None = None,
    spine_mode: str | None = None,
    input_payload: dict[str, Any] | None = None,
    status: AssetAllocationRunStatus = AssetAllocationRunStatus.pending,
    pipeline_source: str = "asset_allocation_pydantic",
) -> uuid.UUID:
    """Write *output* into the ``asset_allocation_*`` tables; return the run id."""
    cs = output.client_summary
    acb = output.asset_class_breakdown

    run = AssetAllocationRun(
        user_id=user_id,
        portfolio_id=portfolio_id,
        chat_session_id=chat_session_id,
        supersedes_id=supersedes_id,
        status=status,
        pipeline_source=pipeline_source,
        spine_mode=spine_mode,
        user_question=user_question,
        rationale=None,
        input_payload=input_payload or {},
        client_age=cs.age,
        client_occupation=cs.occupation,
        client_effective_risk_score=float(cs.effective_risk_score),
        total_corpus=float(cs.total_corpus),
        grand_total=float(output.grand_total),
        all_amounts_in_multiples_of_100=output.all_amounts_in_multiples_of_100,
    )
    db.add(run)
    await db.flush()  # need run.id for children

    bucket: BucketAllocation
    for bucket in output.bucket_allocations:
        future = bucket.future_investment
        bucket_row = AssetAllocationBucket(
            run_id=run.id,
            bucket_name=AllocationBucketName(bucket.bucket),
            total_goal_amount=float(bucket.total_goal_amount),
            allocated_amount=float(bucket.allocated_amount),
            rationale=bucket.rationale,
            future_investment_amount=(
                float(future.future_investment_amount) if future is not None else 0.0
            ),
            future_investment_message=future.message if future is not None else None,
        )
        db.add(bucket_row)
        await db.flush()  # need bucket_row.id for children

        for sub_row in _bucket_subgroup_rows(
            bucket, bucket_row.id, user_id, acb.subgroups
        ):
            db.add(sub_row)

    # Run-level equity / debt / others roll-up: exactly two rows.
    db.add(_aggregate_row(run.id, user_id, AssetClassSplitKind.planned, acb.planned))
    db.add(_aggregate_row(run.id, user_id, AssetClassSplitKind.actual, acb.actual))

    await db.flush()
    return run.id
