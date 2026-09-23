"""Snap-to-current — the last step of the practical allocation (spec 2026-09-21).

Compare the customer's CURRENT holding against the PROPOSED allocation per
`asset_subgroup`. When a subgroup's move is smaller than
`SNAP_THRESHOLD_PCT`% of the entire portfolio, keep the current amount as the
target instead — the customer is not asked to trade for a drift that small.

Snapping moves money off the proposed plan, so the plan no longer sums to the
tradable total. The residual `R = Σ (proposed − current)` over snapped subgroups
is added, in full, to the single un-snapped subgroup with the largest
`|proposed − current|` (Amoul's call: absorb it where the plan is already moving
most, not proportionally). This conserves the tradable total exactly, so
`grand_total` is unchanged.

Frozen sleeves (ELSS, non-MF equity) are never snapped — they are not
MF-tradable. Strict no-op when the caller supplies no current allocation, which
keeps the golden/contract suite byte-identical.

SCOPE: this updates `aggregated_subgroups` — the view rebalancing and the
lifecycle sim read. It intentionally does NOT recompute the display-only
`asset_class_breakdown` / `bucket_allocations` blocks, which continue to show the
pre-snap plan (off by at most the per-subgroup snap thresholds). Recomputing
those faithfully is a separate task; nothing that computes trades reads them.
"""

from __future__ import annotations

import os
from typing import Optional

from asset_allocation_pydantic.models import AggregatedSubgroupRow

SNAP_THRESHOLD_PCT: float = float(
    os.getenv("PAA_ALLOCATION_SNAP_THRESHOLD_PCT", "0.5")
)

# Same frozen set the rebalancing pipeline excludes: these rows carry ELSS and
# direct-stock exposure, which the engine cannot trade per fund.
_FROZEN: frozenset[str] = frozenset({"tax_efficient_equities", "non_mf_equities"})


def _rescale(row: AggregatedSubgroupRow, new_total: float) -> AggregatedSubgroupRow:
    """Return the row at `new_total`, keeping its bucket proportions.

    A row that previously held nothing has no proportions to keep, so the new
    amount lands in `long_term` — the residual bucket most MF subgroups sit in.
    """
    if new_total == row.total:
        return row
    if row.total > 0:
        f = new_total / row.total
        return row.model_copy(
            update={
                "emergency": row.emergency * f,
                "short_term": row.short_term * f,
                "medium_term": row.medium_term * f,
                "long_term": row.long_term * f,
                "total": new_total,
            }
        )
    return row.model_copy(update={"long_term": new_total, "total": new_total})


def snap_rows(
    rows: list[AggregatedSubgroupRow],
    current_subgroup_allocation: Optional[dict[str, float]],
    total_corpus: float,
    threshold_pct: float = SNAP_THRESHOLD_PCT,
) -> list[AggregatedSubgroupRow]:
    """Snap sub-threshold subgroups to current; reconcile onto the biggest mover.

    Returns the same list object unchanged when there is nothing to do, so
    callers can cheaply detect a no-op.
    """
    if not current_subgroup_allocation or total_corpus <= 0:
        return rows

    threshold_inr = threshold_pct / 100.0 * total_corpus
    tradable = [r for r in rows if r.subgroup not in _FROZEN]

    snapped: list[tuple[AggregatedSubgroupRow, float, float]] = []
    unsnapped: list[tuple[AggregatedSubgroupRow, float, float]] = []
    for r in tradable:
        current = float(current_subgroup_allocation.get(r.subgroup, 0.0))
        delta = r.total - current  # proposed − current
        (snapped if abs(delta) < threshold_inr else unsnapped).append(
            (r, current, delta)
        )

    if not snapped:
        return rows

    residual = sum(delta for (_, _, delta) in snapped)  # Σ (proposed − current)

    new_total: dict[str, float] = {}
    for r, current, _ in snapped:
        new_total[r.subgroup] = current
    for r, _, _ in unsnapped:
        new_total[r.subgroup] = r.total  # keep proposed

    if unsnapped:
        # Largest |delta| absorbs the residual; deterministic tie-break.
        recipient = sorted(
            unsnapped, key=lambda t: (-abs(t[2]), -t[0].total, t[0].subgroup)
        )[0][0]
    else:
        # Everything snapped — fall back to the largest current holding.
        recipient = sorted(
            tradable,
            key=lambda r: (-float(current_subgroup_allocation.get(r.subgroup, 0.0)),
                           r.subgroup),
        )[0]
    new_total[recipient.subgroup] = new_total.get(
        recipient.subgroup, recipient.total
    ) + residual

    return [
        _rescale(r, new_total[r.subgroup])
        if r.subgroup in new_total and new_total[r.subgroup] != r.total
        else r
        for r in rows
    ]


def apply_current_allocation_snap(output, current_subgroup_allocation, total_corpus):
    """Snap the assembled output's `aggregated_subgroups`; return a new output.

    No-op (returns the same object) when no current allocation is supplied or
    nothing is within threshold.
    """
    new_rows = snap_rows(
        output.aggregated_subgroups, current_subgroup_allocation, total_corpus
    )
    if new_rows is output.aggregated_subgroups:
        return output
    return output.model_copy(update={"aggregated_subgroups": new_rows})
