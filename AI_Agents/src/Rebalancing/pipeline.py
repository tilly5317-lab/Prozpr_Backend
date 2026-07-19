"""Pipeline orchestrator. Pure-sync, DB-free.

Runs the upstream practical asset-allocation engine first, lifts its
per-subgroup totals onto rank-1 MF rows, then threads the six rebalancing
steps in order. The practical output is also passed through verbatim on
the response for the ideal-vs-practical UI.
"""

from __future__ import annotations

from decimal import Decimal

# Documented cross-agent import per spec §B.1 / §C.3.
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
    PracticalAllocationOutput,
    run_practical_allocation,
)

from .models import (
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingComputeResponse,
)
from .steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step2b_suppress_debt_switch,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
    step5_loss_offset_top_up,
    step6_presentation,
)


# Subgroups that exist in `practical.aggregated_subgroups` but have no MF
# rows in the engine — their amounts are surfaced as frozen
# `SubgroupSummary` entries in step6, not lifted onto rank-1 rows here.
_FROZEN_SUBGROUPS: frozenset[str] = frozenset(
    {
        "tax_efficient_equities",
        "non_mf_equities",
    }
)


def _assign_targets_to_rank1(
    rows: list[FundRowInput],
    practical: PracticalAllocationOutput,
) -> list[FundRowInput]:
    """Return a new list of rows where the rank-1 row of each MF subgroup
    has `target_amount_pre_cap` set to the practical engine's aggregated
    total for that subgroup, offset by the ST portion of any NEUTRAL
    holdings in that subgroup. Rows for frozen subgroups (ELSS, non-MF
    equity) and rank-2+ MF rows are passed through unchanged.

    NEUTRAL ST offset: held funds with `rank == 0` are NEUTRAL — their
    LT portion is migratable to the recommended fund, but the ST portion
    is locked under the "STCG only on force-exit" rule. The locked ST
    counts toward the customer's exposure to the subgroup, so we deduct
    it from the rank-1 target to avoid double-allocating.
    """
    target_by_subgroup: dict[str, Decimal] = {
        r.subgroup: Decimal(str(r.total))
        for r in practical.aggregated_subgroups
        if r.subgroup not in _FROZEN_SUBGROUPS
    }
    neutral_st_by_subgroup: dict[str, Decimal] = {}
    for r in rows:
        if r.rank == 0 and r.asset_subgroup in target_by_subgroup:
            neutral_st_by_subgroup[r.asset_subgroup] = (
                neutral_st_by_subgroup.get(r.asset_subgroup, Decimal(0))
                + r.st_value_inr
            )
    for sg, neutral_st in neutral_st_by_subgroup.items():
        target_by_subgroup[sg] = max(target_by_subgroup[sg] - neutral_st, Decimal(0))
    out: list[FundRowInput] = []
    for r in rows:
        if r.rank == 1 and r.asset_subgroup in target_by_subgroup:
            out.append(
                r.model_copy(
                    update={
                        "target_amount_pre_cap": target_by_subgroup[r.asset_subgroup],
                    }
                )
            )
        else:
            out.append(r)
    return out


def run_rebalancing(request: RebalancingComputeRequest) -> RebalancingComputeResponse:
    # 1. Practical allocation (holdings-aware; consumes ELSS + non-MF scalars).
    practical = run_practical_allocation(request.practical_allocation_input)

    # 2. Lift per-subgroup MF targets onto rank-1 rows.
    rows_with_targets = _assign_targets_to_rank1(request.rows, practical)

    # 3. Six-step rebalancing engine (interface unchanged).
    s1_rows, s1_warnings, unrebalanced_total = step1_cap_and_spill.apply(
        rows_with_targets, request
    )
    s2_rows, s2_warnings = step2_compare_and_decide.apply(s1_rows, request)
    # 2b. Cancel debt-for-debt switches while they are still intents — before
    # any lot selection or tax arithmetic, so those run once on the corrected
    # picture rather than having to be unwound.
    s2b_rows, s2b_warnings = step2b_suppress_debt_switch.apply(s2_rows, request)
    s3_rows = step3_tax_classification.apply(s2b_rows, request)
    # Direct-stock proceeds the NFA band frees up are real spendable cash —
    # step6 surfaces them as SELL_DIRECT_STOCKS, and the practical allocation
    # already assumes they are redeployed. Feed them into step4's pool.
    s4_rows, s4_warnings = step4_initial_trades_under_stcg_cap.apply(
        s3_rows,
        request,
        extra_cash_inr=Decimal(
            str(practical.corpus_breakdown.excess_direct_stocks_inr)
        ),
    )
    s5_rows = step5_loss_offset_top_up.apply(s4_rows, request)

    all_warnings = (
        list(s1_warnings)
        + list(s2_warnings)
        + list(s2b_warnings)
        + list(s4_warnings)
    )
    return step6_presentation.apply(
        s5_rows,
        request,
        all_warnings,
        unrebalanced_total,
        practical=practical,
    )
