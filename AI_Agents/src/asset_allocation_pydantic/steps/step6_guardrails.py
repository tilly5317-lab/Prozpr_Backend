from __future__ import annotations

from ..models import (
    Step4Output,
    Step5Output,
    Step6Output,
    ValidationBlock,
)
from ..tables import (
    EQUITY_SUBGROUPS,
    PHASE1_RISK_BOUNDS,
    PHASE5_EQUITY_SUBGROUP_BOUNDS,
    PHASE5_SHARE_TOLERANCE_PP,
    SUBGROUP_TO_ASSET_CLASS,
)
from ..utils import ceil_to_half


def run(step4: Step4Output, step5: Step5Output, score: float) -> Step6Output:
    violations: list[str] = []
    adjustments: list[str] = []

    alloc = step4.subgroup_amounts
    ac = step4.asset_class_allocation
    row = PHASE1_RISK_BOUNDS[ceil_to_half(score)]

    # Rule 1 — subgroup totals match step4.total_allocated.
    sum_subgroups = sum(alloc.values())
    if sum_subgroups != step4.total_allocated:
        violations.append(
            f"subgroup sum {sum_subgroups} != total_allocated {step4.total_allocated}"
        )

    # Rule 2 — asset class pcts within Phase 1 bounds.
    if not (row.eq_min <= ac.equities_pct <= row.eq_max):
        violations.append(
            f"equities_pct {ac.equities_pct} outside [{row.eq_min}, {row.eq_max}]"
        )
    if not (row.debt_min <= ac.debt_pct <= row.debt_max):
        violations.append(
            f"debt_pct {ac.debt_pct} outside [{row.debt_min}, {row.debt_max}]"
        )
    if not (row.others_min <= ac.others_pct <= row.others_max):
        violations.append(
            f"others_pct {ac.others_pct} outside [{row.others_min}, {row.others_max}]"
        )

    # Rule 3 — equity subgroup shares of equity_for_subgroups within Phase 5 bounds.
    # Phase 5 splits the pool left after ELSS + multi-asset equity carve-out, so
    # the validation must use the same denominator.
    p5 = PHASE5_EQUITY_SUBGROUP_BOUNDS[ceil_to_half(score)]
    equity_pool = step4.multi_asset.equity_for_subgroups
    if equity_pool > 0:
        for sg in EQUITY_SUBGROUPS:
            amt = alloc.get(sg, 0)
            if amt <= 0:
                continue
            share = 100.0 * amt / equity_pool
            lo, hi = p5[sg]
            tol = PHASE5_SHARE_TOLERANCE_PP
            if not (lo - tol <= share <= hi + tol):
                violations.append(
                    f"{sg} share {share:.1f}% of equity_for_subgroups outside [{lo}, {hi}]"
                )

    # Part 2 — Every non-zero aggregated subgroup must roll up to a known asset class.
    for agg_row in step5.rows:
        if agg_row.total > 0 and agg_row.subgroup not in SUBGROUP_TO_ASSET_CLASS:
            violations.append(f"unmapped subgroup: {agg_row.subgroup}")

    all_rules_pass = len(violations) == 0

    return Step6Output(
        validation=ValidationBlock(
            all_rules_pass=all_rules_pass,
            violations_found=violations,
            adjustments_made=adjustments,
        ),
    )
