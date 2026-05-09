"""Step 2 — compare against present holdings and decide what's worth changing.

Spreadsheet refs: cols L (`present_allocation`), M (`diff`), N (`Exit?`),
O (`fund_ratings`), P (`worth_to_change`).

The input rows already have `present_allocation_inr` and other holding
fields populated by the upstream input builder. BAD rows (rank=0,
is_recommended=False) flow through with `final_target_amount=0`, so
their `diff` is just `-present`, `exit_flag` is True, and they're flagged
worth-to-change for full liquidation.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import EXIT_FLOOR_RATING, REBALANCE_MIN_CHANGE_PCT
from ..models import (
    FundRowAfterStep1,
    FundRowAfterStep2,
    RebalancingComputeRequest,
    RebalancingWarning,
    WarningCode,
)


def apply(
    rows: list[FundRowAfterStep1],
    request: RebalancingComputeRequest,
) -> tuple[list[FundRowAfterStep2], list[RebalancingWarning]]:
    _ = request  # not used here; signature kept consistent across steps
    out: list[FundRowAfterStep2] = []
    warnings: list[RebalancingWarning] = []
    threshold_factor = Decimal(str(REBALANCE_MIN_CHANGE_PCT))

    for r in rows:
        diff = r.final_target_amount - r.present_allocation_inr
        exit_flag = (r.fund_rating < EXIT_FLOOR_RATING) or (not r.is_recommended)

        scale = max(r.final_target_amount, r.present_allocation_inr)
        threshold = scale * threshold_factor
        worth_to_change = (abs(diff) >= threshold) or exit_flag

        out.append(
            FundRowAfterStep2(
                **r.model_dump(),
                diff=diff,
                exit_flag=exit_flag,
                worth_to_change=worth_to_change,
            )
        )

        if not r.is_recommended and r.present_allocation_inr > 0:
            warnings.append(
                RebalancingWarning(
                    code=WarningCode.BAD_FUND_DETECTED,
                    message=(
                        f"Held fund {r.isin} ({r.recommended_fund}) is not "
                        f"in the recommended set."
                    ),
                    affected_isins=[r.isin],
                )
            )

    return out, warnings
