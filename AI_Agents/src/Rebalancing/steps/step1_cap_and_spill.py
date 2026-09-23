"""Step 1 — round the assigned target.

Spreadsheet refs (workbook "Allocation 2"): cols F (`allocation_1`),
G (`target_pre_cap_pct`), H (`max_pct`), I (`target_own_capped_pct`),
J (`final_target_pct`), K (`final_target_amount`).

The module keeps its historical name, but since spec 2026-09-20 it neither caps
nor spills: `pipeline._assign_subgroup_targets` decides which 1-2 ranked funds
share a subgroup and in what amounts, and this step rounds that to the request's
rounding step. The per-fund cap no longer bounds deployment, so nothing
overflows and `UNREBALANCED_REMAINDER` is unreachable — the warning list and the
remainder total stay in the signature (the pipeline and step6 bind to them) and
are always empty / zero.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..config import FORCE_EXIT_RANK
from ..models import (
    FundRowAfterStep1,
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingWarning,
)
from ..utils import round_to_step


# No cap bounds deployment any more (spec 2026-09-20). `max_pct` is retained on
# the model for the audit view; 100.0 says "unbounded" rather than naming a
# percentage nothing enforces.
_NO_CAP_PCT: float = 100.0


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
        ranked = sorted(
            [r for r in group if 1 <= r.rank < FORCE_EXIT_RANK],
            key=lambda r: (r.rank, r.isin),
        )
        # Off-list held funds: rank=0 → NEUTRAL (frozen at present);
        # rank=FORCE_EXIT_RANK → force-exit (final target=0).
        neutral = [r for r in group if r.rank == 0]
        force_exit = [r for r in group if r.rank == FORCE_EXIT_RANK]

        for r in ranked:
            # The per-fund cap no longer bounds deployment (spec 2026-09-20):
            # `pipeline._assign_subgroup_targets` has already decided which 1-2
            # funds share this subgroup and in what amounts.
            alloc_3_amount = round_to_step(
                r.target_amount_pre_cap, request.rounding_step
            )
            alloc_3_pct = _pct_of_corpus(alloc_3_amount, corpus)

            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=_NO_CAP_PCT,
                    target_pre_cap_pct=_pct_of_corpus(r.target_amount_pre_cap, corpus),
                    target_own_capped_pct=alloc_3_pct,
                    final_target_pct=alloc_3_pct,
                    final_target_amount=alloc_3_amount,
                )
            )

        # NEUTRAL rows preserve `target_amount_pre_cap` (= present holding,
        # set by the input builder) so step2 produces `diff = 0` and the
        # holding is left untouched.
        for r in neutral:
            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=_NO_CAP_PCT,
                    target_pre_cap_pct=_pct_of_corpus(r.target_amount_pre_cap, corpus),
                    target_own_capped_pct=_pct_of_corpus(
                        r.target_amount_pre_cap, corpus
                    ),
                    final_target_pct=_pct_of_corpus(r.target_amount_pre_cap, corpus),
                    final_target_amount=r.target_amount_pre_cap,
                )
            )

        # Force-exit rows always emit final_target = 0; step2 sees diff =
        # -present and exit_flag = True, step4 fully liquidates them.
        for r in force_exit:
            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=_NO_CAP_PCT,
                    target_pre_cap_pct=0.0,
                    target_own_capped_pct=0.0,
                    final_target_pct=0.0,
                    final_target_amount=Decimal(0),
                )
            )

    return out, warnings, unrebalanced_total
