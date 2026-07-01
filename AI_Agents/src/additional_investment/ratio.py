"""Bucket-targeted subgroup split for additional investment. Pure, no state, no I/O.

The deposit is deployed toward the nearest unfunded goal: subgroups are weighted by
that horizon bucket's column (short / medium / long-term), renormalised to a ratio,
and the deploy amount is split by it. Emergency is never a target.
"""

from __future__ import annotations

from .models import (
    SubgroupBucketAmounts,
    SubgroupTarget,
    TargetBucket,
)


def select_target_bucket(short_term_fulfilled: bool, medium_term_fulfilled: bool) -> TargetBucket:
    """First unfunded bucket in priority short → medium → long.

    Long-term is the target whenever short and medium are both fulfilled — whether
    long-term itself is still unfunded or every goal is funded (keep building long-term).
    """
    if not short_term_fulfilled:
        return TargetBucket.SHORT_TERM
    if not medium_term_fulfilled:
        return TargetBucket.MEDIUM_TERM
    return TargetBucket.LONG_TERM


def _bucket_weight(row: SubgroupBucketAmounts, bucket: TargetBucket) -> float:
    """Per-subgroup weight = its amount in the target bucket's column."""
    return max(getattr(row, bucket.value), 0.0)


def compute_targets(
    subgroups: list[SubgroupBucketAmounts],
    short_term_fulfilled: bool,
    medium_term_fulfilled: bool,
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> tuple[TargetBucket, list[SubgroupTarget]]:
    """Weight subgroups by the target bucket's column, renormalise to ratios, and split the deploy amount.

    Subgroups in `exclude_subgroups` get zero weight, so they receive no target and
    their share renormalises onto the remaining (eligible) subgroups.
    """
    bucket = select_target_bucket(short_term_fulfilled, medium_term_fulfilled)
    weights = {
        r.subgroup: (0.0 if r.subgroup in exclude_subgroups else _bucket_weight(r, bucket))
        for r in subgroups
    }
    total_weight = sum(weights.values())
    targets: list[SubgroupTarget] = []
    if total_weight <= 0:
        return bucket, targets
    for row in subgroups:
        w = weights[row.subgroup]
        if w <= 0:
            continue
        ratio = w / total_weight
        targets.append(
            SubgroupTarget(subgroup=row.subgroup, ratio=ratio, target_inr=ratio * deploy_amount)
        )
    return bucket, targets
