"""Step 2 — compare against present holdings and decide what's worth changing.

Spreadsheet refs: cols L (`present_allocation`), M (`diff`), N (`Exit?`),
O (`fund_ratings`), P (`worth_to_change`).

Off-list holdings come in two flavours from the input builder:
* Force-exit rows (`rank == FORCE_EXIT_RANK`, `target_amount_pre_cap = 0`)
  flow through with `diff = -present`, `exit_flag = True`, and are flagged
  worth-to-change for full liquidation by step4 (LT + ST).
* NEUTRAL rows (`rank = 0`, `target_amount_pre_cap = st_value_inr`) have
  `diff = -lt_value`, `exit_flag = False`. step4's optional pool taps
  their LT bucket only when buy demand calls for it; the ST portion
  stays put (the "STCG only on force-exit" rule).
"""

from __future__ import annotations

from decimal import Decimal

from ..config import EXIT_FLOOR_RATING, FORCE_EXIT_RANK, REBALANCE_MIN_CHANGE_PCT
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
        # Per C.4 (spec 2026-05-23): the upstream input builder strips ELSS
        # rows out of `rows`; ELSS exposure now travels as a scalar on
        # `practical_allocation_input.elss_corpus` and surfaces as a frozen
        # SubgroupSummary in step6. Step2 therefore has a single uniform
        # diff/exit/worth-to-change path.
        diff = r.final_target_amount - r.present_allocation_inr
        exit_flag = (r.fund_rating < EXIT_FLOOR_RATING) or (r.rank == FORCE_EXIT_RANK)

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

        if r.rank == FORCE_EXIT_RANK and r.present_allocation_inr > 0:
            warnings.append(
                RebalancingWarning(
                    code=WarningCode.BAD_FUND_DETECTED,
                    message=(
                        f"Held fund {r.isin} ({r.recommended_fund}) is on the "
                        f"force-exit list."
                    ),
                    affected_isins=[r.isin],
                )
            )

    return out, warnings
