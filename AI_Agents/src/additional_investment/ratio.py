from __future__ import annotations

from additional_investment.models import (
    BranchUsed,
    SubgroupBucketAmounts,
    SubgroupTarget,
)


def compute_branch(medium_term_fulfilled: bool) -> BranchUsed:
    return BranchUsed.LONG_TERM if medium_term_fulfilled else BranchUsed.TOTAL_MINUS_EMERGENCY


def _weight(row: SubgroupBucketAmounts, branch: BranchUsed) -> float:
    if branch is BranchUsed.LONG_TERM:
        return max(row.long_term, 0.0)
    return max(row.total - row.emergency, 0.0)


def compute_targets(
    subgroups: list[SubgroupBucketAmounts],
    medium_term_fulfilled: bool,
    deploy_amount: float,
) -> tuple[BranchUsed, list[SubgroupTarget]]:
    branch = compute_branch(medium_term_fulfilled)
    weights = {r.subgroup: _weight(r, branch) for r in subgroups}
    total_weight = sum(weights.values())
    targets: list[SubgroupTarget] = []
    if total_weight <= 0:
        return branch, targets
    for row in subgroups:
        w = weights[row.subgroup]
        if w <= 0:
            continue
        ratio = w / total_weight
        targets.append(
            SubgroupTarget(subgroup=row.subgroup, ratio=ratio, target_inr=ratio * deploy_amount)
        )
    return branch, targets
