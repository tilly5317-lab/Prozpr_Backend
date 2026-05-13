"""Insert bucket rows and their children: goal links, subgroups, asset-class splits.

Each bucket from ``doc["bucket_allocations"]`` produces:
  1. ``asset_allocation_buckets``          — the bucket header
  2. ``asset_allocation_bucket_run_targets`` — M:N links to run targets (goals)
  3. ``asset_allocation_bucket_subgroups``  — subgroup amounts inside the bucket
  4. ``asset_allocation_bucket_asset_classes`` — equity/debt/others split (planned + actual)
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_allocation.bucket import (
    AllocationBucketName,
    AssetAllocationBucket,
    AssetAllocationBucketAssetClass,
    AssetAllocationBucketRunTarget,
    AssetAllocationBucketSubgroup,
    AssetClassSplitKind,
)
from app.models.asset_allocation.run import AssetAllocationRun


# ── Helpers ─────────────────────────────────────────────────────────────


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_bucket_name(raw: str | None) -> AllocationBucketName:
    if not raw:
        raise ValueError("bucket name missing")
    try:
        return AllocationBucketName(str(raw).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unknown allocation bucket name: {raw!r}") from exc


def _find_per_bucket_row(per_bucket: list[Any] | None, key: str) -> dict[str, Any] | None:
    """Find the entry in a ``per_bucket`` list whose ``bucket`` matches *key*."""
    for row in per_bucket or []:
        if isinstance(row, dict) and str(row.get("bucket") or "").lower() == key:
            return row
    return None


def _subgroup_lists_for_bucket(
    doc: dict[str, Any], bucket_key: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(planned_rows, actual_rows)`` for a bucket from ``asset_class_breakdown.subgroups``."""
    sub_root = (doc.get("asset_class_breakdown") or {}).get("subgroups") or {}
    if not isinstance(sub_root, dict):
        return [], []

    def _extract(blocks: list[Any]) -> list[dict[str, Any]]:
        for block in blocks:
            if isinstance(block, dict) and str(block.get("bucket") or "").lower() == bucket_key:
                return [x for x in (block.get("subgroups") or []) if isinstance(x, dict)]
        return []

    return (
        _extract(list(sub_root.get("planned") or [])),
        _extract(list(sub_root.get("actual") or [])),
    )


# ── Per-bucket child writers ────────────────────────────────────────────


async def _link_goals_to_bucket(
    db: AsyncSession,
    bucket: AssetAllocationBucket,
    bucket_block: dict[str, Any],
    target_name_to_id: dict[str, uuid.UUID],
) -> None:
    """Insert ``asset_allocation_bucket_run_targets`` for this bucket."""
    rationales = bucket_block.get("goal_rationales") or {}
    if not isinstance(rationales, dict):
        rationales = {}

    for goal_blob in bucket_block.get("goals") or []:
        if not isinstance(goal_blob, dict):
            continue
        name = str(goal_blob.get("goal_name") or "").strip()
        target_id = target_name_to_id.get(name)
        if target_id is None:
            continue

        rationale_text = rationales.get(name)
        db.add(
            AssetAllocationBucketRunTarget(
                bucket_id=bucket.id,
                run_target_id=target_id,
                goal_rationale=str(rationale_text) if rationale_text is not None else None,
            )
        )
    await db.flush()


async def _insert_subgroups(
    db: AsyncSession,
    bucket: AssetAllocationBucket,
    bucket_block: dict[str, Any],
    doc: dict[str, Any],
    bucket_key: str,
) -> None:
    """Insert ``asset_allocation_bucket_subgroups`` for this bucket.

    Prefers the detailed ``planned`` / ``actual`` subgroup blocks from
    ``asset_class_breakdown.subgroups``; falls back to the flat
    ``subgroup_amounts`` dict on the bucket block.
    """
    allocated = _float(bucket_block.get("allocated_amount")) or 0.0
    flat_amounts = bucket_block.get("subgroup_amounts") or {}
    if not isinstance(flat_amounts, dict):
        flat_amounts = {}

    planned_rows, actual_rows = _subgroup_lists_for_bucket(doc, bucket_key)

    if planned_rows or actual_rows:
        _write_detailed_subgroups(db, bucket, planned_rows, actual_rows, flat_amounts, allocated)
    else:
        _write_flat_subgroups(db, bucket, flat_amounts, allocated)

    await db.flush()


def _write_detailed_subgroups(
    db: AsyncSession,
    bucket: AssetAllocationBucket,
    planned_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    flat_amounts: dict[str, Any],
    allocated: float,
) -> None:
    by_planned = {str(r.get("subgroup")): r for r in planned_rows if r.get("subgroup")}
    by_actual = {str(r.get("subgroup")): r for r in actual_rows if r.get("subgroup")}
    all_subgroups = sorted(set(by_planned) | set(by_actual) | set(flat_amounts))

    for sg in all_subgroups:
        pr = by_planned.get(sg, {})
        ar = by_actual.get(sg, {})

        p_amt = _float(pr.get("amount")) if pr else _float(flat_amounts.get(sg))
        a_amt = _float(ar.get("amount")) if ar else _float(flat_amounts.get(sg))

        db.add(
            AssetAllocationBucketSubgroup(
                bucket_id=bucket.id,
                subgroup=sg[:80],
                planned_amount=p_amt,
                actual_amount=a_amt,
                planned_pct_of_bucket=_pct(pr.get("pct_of_bucket"), p_amt, allocated),
                actual_pct_of_bucket=_pct(ar.get("pct_of_bucket"), a_amt, allocated),
            )
        )


def _write_flat_subgroups(
    db: AsyncSession,
    bucket: AssetAllocationBucket,
    flat_amounts: dict[str, Any],
    allocated: float,
) -> None:
    for sg, raw_amt in flat_amounts.items():
        amt = _float(raw_amt)
        pct = 100.0 * amt / allocated if allocated else None
        db.add(
            AssetAllocationBucketSubgroup(
                bucket_id=bucket.id,
                subgroup=str(sg)[:80],
                planned_amount=amt,
                actual_amount=amt,
                planned_pct_of_bucket=pct,
                actual_pct_of_bucket=pct,
            )
        )


def _pct(explicit: Any, amount: float, total: float) -> float | None:
    """Return explicit pct if given, else compute from amount/total."""
    if explicit is not None:
        return _float(explicit)
    return 100.0 * amount / total if total else None


async def _insert_asset_class_splits(
    db: AsyncSession,
    bucket: AssetAllocationBucket,
    planned_per: list[Any],
    actual_per: list[Any],
    bucket_key: str,
) -> None:
    """Insert ``asset_allocation_bucket_asset_classes`` (planned + actual) for this bucket."""
    for per_list, kind in [
        (planned_per, AssetClassSplitKind.planned),
        (actual_per, AssetClassSplitKind.actual),
    ]:
        row = _find_per_bucket_row(per_list, bucket_key)
        if row is None:
            continue
        db.add(
            AssetAllocationBucketAssetClass(
                bucket_id=bucket.id,
                split_kind=kind,
                equity_amount=_float(row.get("equity")),
                debt_amount=_float(row.get("debt")),
                others_amount=_float(row.get("others")),
                equity_pct=_float(row.get("equity_pct")),
                debt_pct=_float(row.get("debt_pct")),
                others_pct=_float(row.get("others_pct")),
            )
        )
    await db.flush()


# ── Top-level entry point ──────────────────────────────────────────────


async def insert_buckets_and_children(
    db: AsyncSession,
    run: AssetAllocationRun,
    doc: dict[str, Any],
    target_name_to_snapshot_id: dict[str, uuid.UUID],
) -> None:
    """Insert all buckets for *run* from *doc*, including children."""
    breakdown = doc.get("asset_class_breakdown") or {}
    planned_per = list((breakdown.get("planned") or {}).get("per_bucket") or [])
    actual_per = list((breakdown.get("actual") or {}).get("per_bucket") or [])

    for bucket_block in doc.get("bucket_allocations") or []:
        if not isinstance(bucket_block, dict):
            continue
        bkey = str(bucket_block.get("bucket") or "").lower()
        if not bkey:
            continue

        fi = bucket_block.get("future_investment") or {}
        fi_msg = fi.get("message")

        bucket = AssetAllocationBucket(
            run_id=run.id,
            bucket_name=_parse_bucket_name(bkey),
            total_goal_amount=_float(bucket_block.get("total_goal_amount")),
            allocated_amount=_float(bucket_block.get("allocated_amount")),
            rationale=(str(r) if (r := bucket_block.get("rationale")) else None),
            future_investment_amount=_float(fi.get("future_investment_amount")),
            future_investment_message=(str(fi_msg) if fi_msg else None),
        )
        db.add(bucket)
        await db.flush()

        await _link_goals_to_bucket(db, bucket, bucket_block, target_name_to_snapshot_id)
        await _insert_subgroups(db, bucket, bucket_block, doc, bkey)
        await _insert_asset_class_splits(db, bucket, planned_per, actual_per, bkey)
