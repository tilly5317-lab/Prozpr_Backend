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

# Per-fund cap lookup moved to tables.cap_pct_for; config constants are still
# read inside that helper.
from ..config import FORCE_EXIT_RANK, FUND_CAP_FLOOR_INR
from ..models import (
    FundRowAfterStep1,
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingWarning,
    WarningCode,
)
from ..tables import cap_pct_for, effective_cap_for
from ..utils import round_to_step


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

        spill_in = [Decimal(0)] * len(ranked)

        for i, r in enumerate(ranked):
            # Per-fund cap = max(pct × corpus, rupee floor) — the floor keeps
            # a small portfolio out of sub-₹1L fund fragments (amendment
            # 2026-07-06). max_pct reports the EFFECTIVE cap when the floor
            # wins, so the audit trail matches the amounts.
            # The per-fund cap governs where NEW money is deployed, not what the
            # customer is forced to sell (design note 2026-07-19, decision 1).
            # A protected holding raises the ceiling for its own row only —
            # `protected_floor_inr` is set by the pipeline's target assignment
            # and is zero for anything the rank band declines to protect, so an
            # unprotected over-cap holding is still trimmed exactly as before.
            cap_amount = max(
                effective_cap_for(r.asset_subgroup, corpus), r.protected_floor_inr
            )
            # `max_pct` reports the EFFECTIVE cap, matching the precedent set by
            # the rupee floor (see the module docstring and CLAUDE.md) — the
            # audit column must not contradict the amounts beside it.
            max_pct = _pct_of_corpus(cap_amount, corpus)

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

        # NEUTRAL rows preserve `target_amount_pre_cap` (= present holding,
        # set by the input builder) so step2 produces `diff = 0` and the
        # holding is left untouched.
        for r in neutral:
            out.append(
                FundRowAfterStep1(
                    **r.model_dump(),
                    max_pct=cap_pct_for(r.asset_subgroup),
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
                    max_pct=cap_pct_for(r.asset_subgroup),
                    target_pre_cap_pct=0.0,
                    target_own_capped_pct=0.0,
                    final_target_pct=0.0,
                    final_target_amount=Decimal(0),
                )
            )

    return out, warnings, unrebalanced_total
