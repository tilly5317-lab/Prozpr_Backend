"""Step 1 — per-fund cap & spill.

Spreadsheet refs (workbook "Allocation 2"): cols F (`allocation_1`),
G (`target_pre_cap_pct`), H (`max_pct`), I (`target_own_capped_pct`),
J (`final_target_pct`), K (`final_target_amount`).

Walks ranks 1, 2, 3, … within each `asset_subgroup`. Caps each fund at
`max_pct × corpus`; pushes any overflow forward to the next rank's
pre-cap target. Residual after the last rank surfaces as a warning + an
`unrebalanced_remainder_inr` total — never silently dropped.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..config import MULTI_FUND_CAP_PCT, OTHERS_FUND_CAP_PCT
from ..models import (
    FundRowAfterStep1,
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingWarning,
    WarningCode,
)
from ..tables import MULTI_CAP_SUB_CATEGORIES
from ..utils import round_to_step


def _max_pct_for(sub_category: str) -> float:
    return MULTI_FUND_CAP_PCT if sub_category in MULTI_CAP_SUB_CATEGORIES else OTHERS_FUND_CAP_PCT


def _pct_of_corpus(amount: Decimal, corpus: Decimal) -> float:
    if corpus <= 0:
        return 0.0
    return float(amount / corpus * Decimal(100))


def apply(
    rows: list[FundRowInput],
    request: RebalancingComputeRequest,
) -> tuple[list[FundRowAfterStep1], list[RebalancingWarning], Decimal]:
    """Returns (rows_after_step_1, warnings, unrebalanced_remainder_inr)."""
    corpus = request.total_corpus
    by_sg: dict[str, list[FundRowInput]] = defaultdict(list)
    for r in rows:
        by_sg[r.asset_subgroup].append(r)

    out: list[FundRowAfterStep1] = []
    warnings: list[RebalancingWarning] = []
    unrebalanced_total = Decimal(0)

    for sg, group in by_sg.items():
        ranked = sorted([r for r in group if r.rank >= 1], key=lambda r: r.rank)
        bad = [r for r in group if r.rank == 0]

        spill_in = [Decimal(0)] * len(ranked)

        for i, r in enumerate(ranked):
            max_pct = _max_pct_for(r.sub_category)
            cap_amount = Decimal(str(max_pct)) / Decimal(100) * corpus

            own_capped = min(r.target_amount_pre_cap, cap_amount)
            with_spill = r.target_amount_pre_cap + spill_in[i]

            if with_spill > cap_amount:
                alloc_3_raw = cap_amount
                overflow = with_spill - cap_amount
                if i + 1 < len(ranked):
                    spill_in[i + 1] += overflow
                else:
                    unrebalanced_total += overflow
                    warnings.append(
                        RebalancingWarning(
                            code=WarningCode.UNREBALANCED_REMAINDER,
                            message=(
                                f"Subgroup '{sg}' has ₹{overflow} above "
                                f"available rank caps."
                            ),
                            affected_isins=[r.isin],
                        )
                    )
            else:
                alloc_3_raw = with_spill

            alloc_3_amount = round_to_step(alloc_3_raw, request.rounding_step)

            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=max_pct,
                    target_pre_cap_pct=_pct_of_corpus(r.target_amount_pre_cap, corpus),
                    target_own_capped_pct=_pct_of_corpus(own_capped, corpus),
                    final_target_pct=_pct_of_corpus(alloc_3_amount, corpus),
                    final_target_amount=alloc_3_amount,
                )
            )

        for r in bad:
            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=_max_pct_for(r.sub_category),
                    target_pre_cap_pct=0.0,
                    target_own_capped_pct=0.0,
                    final_target_pct=0.0,
                    final_target_amount=Decimal(0),
                )
            )

    return out, warnings, unrebalanced_total
