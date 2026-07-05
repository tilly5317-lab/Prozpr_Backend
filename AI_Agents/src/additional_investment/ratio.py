"""Subgroup splits for additional investment. Pure, no state, no I/O.

Two split modes live here. LEGACY (SIP, or lumpsum without a holdings map): the
deposit is deployed toward the nearest unfunded goal — subgroups are weighted by
that horizon bucket's column (short / medium / long-term), renormalised to a
ratio, and the deploy amount is split by it; emergency is never a target.
DEFICIT FILL (lumpsum with a holdings map, spec 2026-07-03): the deposit fills
the gaps between the post-investment ideal and current holdings.
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


def compute_deficit_targets(
    subgroups: list[SubgroupBucketAmounts],
    current_by_subgroup: dict[str, float],
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> list[SubgroupTarget]:
    """Deficit-fill split for a one-time lumpsum (holdings-aware, buy-only).

    ideal_i is each ELIGIBLE subgroup's ``total`` column (the post-investment
    practical allocation — the caller ran PAA at corpus + deploy_amount).
    deficit_i = max(0, ideal_i - current_i); the deploy amount is split across
    positive deficits proportionally. ratio_i = target_i / deploy_amount, so the
    legacy identity ``target_inr = ratio * deploy_amount`` holds in both modes.

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


def dominant_bucket(
    targets: list[SubgroupTarget],
    subgroups: list[SubgroupBucketAmounts],
) -> TargetBucket:
    """Horizon that receives the most deployed money — the deficit-mode label.

    Each target's rupees are apportioned to short/medium/long by its subgroup's
    horizon composition (bucket column / total). Deterministic tie-break: the
    iteration order below means LONG_TERM wins ties (and the empty case).
    """
    rows = {r.subgroup: r for r in subgroups}
    order = (TargetBucket.LONG_TERM, TargetBucket.MEDIUM_TERM, TargetBucket.SHORT_TERM)
    scores = {b: 0.0 for b in order}
    for t in targets:
        row = rows.get(t.subgroup)
        if row is None or row.total <= 0:
            continue
        for b in order:
            scores[b] += t.target_inr * (max(getattr(row, b.value), 0.0) / row.total)
    best = order[0]
    for b in order:
        if scores[b] > scores[best]:
            best = b
    return best
