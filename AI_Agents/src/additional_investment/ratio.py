"""Subgroup splits for additional investment. Pure, no state, no I/O.

Goal money first: the goal share goes to the routed short-term subgroup by name.
The rest follows the long-term column (SIP), or — with a holdings map (lumpsum)
— the long-term deficits: each row's total minus its short-term column, against
the caller's map of current holdings with the goal-used held short-term money
removed.
"""

from __future__ import annotations

from .models import (
    SubgroupBucketAmounts,
    SubgroupTarget,
    TargetBucket,
)


def compute_deficit_targets(
    subgroups: list[SubgroupBucketAmounts],
    current_by_subgroup: dict[str, float],
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> list[SubgroupTarget]:
    """Deficit-fill split for a one-time lumpsum (holdings-aware, buy-only).

    ideal_i is each ELIGIBLE row's ``total`` — the caller passes long-term rows
    (total − short_term of the post-investment practical allocation).
    deficit_i = max(0, ideal_i − current_i), split proportionally;
    ratio_i = target_i / deploy_amount.

    CONTRACT: iterate the IDEAL rows and look up current values with
    ``current_by_subgroup.get(subgroup, 0.0)`` — never the reverse. A held
    subgroup with no ideal row is thereby overweight by construction (no buy,
    no error); its value still shaped the caller's corpus total.

    Fallback: when every eligible deficit is zero (at/above ideal everywhere),
    distribute by the eligible ideal ratios instead — keeps building toward the
    ideal rather than deploying nothing.
    """
    eligible = [r for r in subgroups if r.subgroup not in exclude_subgroups]
    deficits = {
        r.subgroup: max(0.0, r.total - current_by_subgroup.get(r.subgroup, 0.0))
        for r in eligible
    }
    total_deficit = sum(deficits.values())
    if total_deficit <= 0:
        total_ideal = sum(max(r.total, 0.0) for r in eligible)
        if total_ideal <= 0:
            return []
        return [
            SubgroupTarget(
                subgroup=r.subgroup,
                ratio=max(r.total, 0.0) / total_ideal,
                target_inr=(max(r.total, 0.0) / total_ideal) * deploy_amount,
            )
            for r in eligible
            if max(r.total, 0.0) > 0
        ]
    targets: list[SubgroupTarget] = []
    for r in eligible:
        d = deficits[r.subgroup]
        if d <= 0:
            continue
        ratio = d / total_deficit
        targets.append(
            SubgroupTarget(subgroup=r.subgroup, ratio=ratio, target_inr=ratio * deploy_amount)
        )
    return targets


def compute_long_term_targets(
    subgroups: list[SubgroupBucketAmounts],
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> list[SubgroupTarget]:
    """Split by each eligible subgroup's long-term column, renormalised."""
    weights = {
        r.subgroup: 0.0 if r.subgroup in exclude_subgroups else max(r.long_term, 0.0)
        for r in subgroups
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return []
    return [
        SubgroupTarget(
            subgroup=r.subgroup,
            ratio=weights[r.subgroup] / total_weight,
            target_inr=weights[r.subgroup] / total_weight * deploy_amount,
        )
        for r in subgroups
        if weights[r.subgroup] > 0
    ]


def compute_goal_first_targets(
    subgroups: list[SubgroupBucketAmounts],
    deploy_amount: float,
    goal_share: float,
    goal_subgroup: str | None,
    exclude_subgroups: set[str] = frozenset(),
    current_by_subgroup: dict[str, float] | None = None,
) -> tuple[TargetBucket, list[SubgroupTarget]]:
    """Goal money first, into goal_subgroup by name — never weighted by the
    short-term column, which is empty when the plan holds no short-term money."""
    goal = min(goal_share, deploy_amount) if goal_subgroup else 0.0
    rest = deploy_amount - goal
    rest_targets: list[SubgroupTarget] = []
    if rest > 0:
        if current_by_subgroup is None:
            rest_targets = compute_long_term_targets(subgroups, rest, exclude_subgroups)
        else:
            long_term_rows = [
                r.model_copy(update={"total": max(0.0, r.total - r.short_term), "short_term": 0.0})
                for r in subgroups
            ]
            rest_targets = compute_deficit_targets(
                long_term_rows, current_by_subgroup, rest, exclude_subgroups
            )
    amounts: dict[str, float] = {}
    if goal > 0:
        amounts[goal_subgroup] = goal
    for t in rest_targets:
        amounts[t.subgroup] = amounts.get(t.subgroup, 0.0) + t.target_inr
    targets = [
        SubgroupTarget(subgroup=sg, ratio=amt / deploy_amount, target_inr=amt)
        for sg, amt in amounts.items()
    ]
    bucket = TargetBucket.SHORT_TERM if goal * 2 >= deploy_amount else TargetBucket.LONG_TERM
    return bucket, targets
