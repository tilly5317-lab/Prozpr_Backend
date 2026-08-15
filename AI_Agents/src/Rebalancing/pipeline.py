"""Pipeline orchestrator. Pure-sync, DB-free.

Runs the upstream practical asset-allocation engine first, lifts its
per-subgroup totals onto rank-1 MF rows, then threads the six rebalancing
steps in order. The practical output is also passed through verbatim on
the response for the ideal-vs-practical UI.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

# Documented cross-agent import per spec §B.1 / §C.3.
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
    PracticalAllocationOutput,
    run_practical_allocation,
)

from .config import (
    EXIT_FLOOR_RATING,
    FORCE_EXIT_RANK,
    HOLDINGS_AWARE_TARGETS_ENABLED,
    RANK_PROTECT_BAND,
)
from .models import (
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingComputeResponse,
)
from .utils import floor_to_step
from .steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step2b_suppress_debt_switch,
    step2c_suppress_small_sells,
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


def _protected_floors(
    rows: list[FundRowInput],
    target_by_subgroup: dict[str, Decimal],
    rounding_step: int,
) -> dict[str, Decimal]:
    """Return `{isin: floor}` — the amount each held fund is allowed to keep.

    A held recommended fund keeps its holding as its target when the gap to the
    best-ranked fund in its subgroup is UNDER `RANK_PROTECT_BAND` (exclusive:
    gap 4 protects, gap 5 sells). Everything else gets 0, which is today's
    behaviour — `input_builder.py:272` assigns a target only to rank-1.

    Carve-outs, all by structural exclusion rather than by the band predicate:

    * `rank == 0` (off-list). Rank 0 means "not in the recommended set", not
      "rank zero". A naive `abs(0 - 1) < 5` protects them and kills the engine's
      core off-list migration; excluding them from `ranked` makes that
      unreachable. They keep their existing LT-only migration path, which also
      preserves the STCG protection on recently-bought units.
    * `rank == FORCE_EXIT_RANK`. Excluded for the same reason step1 excludes it.
    * `fund_rating < EXIT_FLOOR_RATING`. `exit_flag` is computed in step2, AFTER
      target assignment, so the rating test must be replicated here. A protected
      row that later gets `exit_flag` emits contradictory output: step4 sells it
      in full while step6 reports `goal_target = present`.

    Oversubscription (holdings above the subgroup target) is trimmed
    worst-rank-first, so a customer over-allocated across several funds loses
    the poorest-ranked position first.
    """
    if not HOLDINGS_AWARE_TARGETS_ENABLED:
        return {r.isin: Decimal(0) for r in rows}

    by_sg: dict[str, list[FundRowInput]] = defaultdict(list)
    for r in rows:
        if 1 <= r.rank < FORCE_EXIT_RANK and r.asset_subgroup in target_by_subgroup:
            by_sg[r.asset_subgroup].append(r)

    floors: dict[str, Decimal] = {r.isin: Decimal(0) for r in rows}
    for sg, ranked in by_sg.items():
        ranked.sort(key=lambda r: (r.rank, r.isin))
        # Measure the gap from the best RANKED fund present, not from a
        # hardcoded 1 — a subgroup's ladder need not start at rank 1.
        best = min(r.rank for r in ranked)
        target = target_by_subgroup[sg]

        for r in ranked:
            protected = (
                r.present_allocation_inr > 0
                and (r.rank - best) < RANK_PROTECT_BAND
                and r.fund_rating >= EXIT_FLOOR_RATING
            )
            # Floor DOWN to the rounding step: step1 rounds targets to the
            # nearest ₹100, which would otherwise leave a phantom `diff` of up
            # to half a step on a row that is supposed to be untouched.
            floors[r.isin] = (
                floor_to_step(r.present_allocation_inr, rounding_step)
                if protected
                else Decimal(0)
            )

        excess = sum((floors[r.isin] for r in ranked), Decimal(0)) - target
        for r in sorted(ranked, key=lambda x: (-x.rank, x.isin)):
            if excess <= 0:
                break
            cut = min(floors[r.isin], excess)
            floors[r.isin] -= cut
            excess -= cut

    return floors


def _assign_subgroup_targets(
    rows: list[FundRowInput],
    practical: PracticalAllocationOutput,
    rounding_step: int,
) -> list[FundRowInput]:
    """Split each MF subgroup's practical total across its ranked rows.

    RESERVE-THEN-RESIDUAL (design note 2026-07-19). Each held fund inside the
    rank band first reserves what it already holds; only the leftover is fresh
    money, and that lands on the best-ranked row to cascade down step1's ladder.

    Before this change every rank >= 2 row got `target_amount_pre_cap = 0` and
    was therefore sold to fund a rank-1 buy — ₹16.5L of paired churn in a single
    simulated rebalance, against the 69.4% of real held rows that sit at rank >= 2.
    With `HOLDINGS_AWARE_TARGETS_ENABLED = False` every floor is zero, the
    residual collapses back to the full subgroup total, and the behaviour is
    byte-identical to that older rule.

    Rows for frozen subgroups (ELSS, non-MF equity), off-list rows (`rank == 0`)
    and force-exit rows are passed through unchanged.

    NEUTRAL ST offset: held funds with `rank == 0` are NEUTRAL — their
    LT portion is migratable to the recommended fund, but the ST portion
    is locked under the "STCG only on force-exit" rule. The locked ST
    counts toward the customer's exposure to the subgroup, so we deduct
    it from the subgroup target to avoid double-allocating.
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

    # RESERVE: what each held fund keeps. Computed against the ALREADY-reduced
    # target, so floors carve out of what is left after the NEUTRAL ST offset
    # rather than competing with it.
    floors = _protected_floors(rows, target_by_subgroup, rounding_step)

    floor_total: dict[str, Decimal] = defaultdict(Decimal)
    best_rank: dict[str, int] = {}
    for r in rows:
        if not (1 <= r.rank < FORCE_EXIT_RANK) or r.asset_subgroup not in target_by_subgroup:
            continue
        floor_total[r.asset_subgroup] += floors.get(r.isin, Decimal(0))
        cur = best_rank.get(r.asset_subgroup)
        if cur is None or r.rank < cur:
            best_rank[r.asset_subgroup] = r.rank

    out: list[FundRowInput] = []
    for r in rows:
        sg = r.asset_subgroup
        if sg not in target_by_subgroup or not (1 <= r.rank < FORCE_EXIT_RANK):
            out.append(r)
            continue

        floor = floors.get(r.isin, Decimal(0))
        # RESIDUAL: only what is not already spoken for by a protected holding.
        # It lands on the best-ranked row and cascades down the ladder in step1,
        # which is what makes NEW allocation dilute an over-weight rank-1 rather
        # than topping it up further.
        residual = max(target_by_subgroup[sg] - floor_total[sg], Decimal(0))
        target = floor + residual if r.rank == best_rank[sg] else floor

        out.append(
            r.model_copy(
                update={
                    "target_amount_pre_cap": target,
                    "protected_floor_inr": floor,
                }
            )
        )
    return out


def run_rebalancing(request: RebalancingComputeRequest) -> RebalancingComputeResponse:
    # 1. Practical allocation (holdings-aware; consumes ELSS + non-MF scalars).
    practical = run_practical_allocation(request.practical_allocation_input)

    # 2. Split per-subgroup MF targets across ranked rows: held funds inside the
    #    rank band reserve what they hold, the residual goes to the best rank.
    rows_with_targets = _assign_subgroup_targets(
        request.rows, practical, request.rounding_step
    )

    # 3. Six-step rebalancing engine (interface unchanged).
    s1_rows, s1_warnings, unrebalanced_total = step1_cap_and_spill.apply(
        rows_with_targets, request
    )
    s2_rows, s2_warnings = step2_compare_and_decide.apply(s1_rows, request)
    # 2b. Cancel debt-for-debt switches while they are still intents — before
    # any lot selection or tax arithmetic, so those run once on the corrected
    # picture rather than having to be unwound.
    s2b_rows, s2b_warnings = step2b_suppress_debt_switch.apply(s2_rows, request)
    # 2c. Cancel optional sells we don't want to place (sub-0.5% trims, and
    # recently-bought funds) — after netting, before any tax/lot selection.
    s2c_rows, s2c_warnings = step2c_suppress_small_sells.apply(s2b_rows, request)
    s3_rows = step3_tax_classification.apply(s2c_rows, request)
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
        + list(s2c_warnings)
        + list(s4_warnings)
    )
    return step6_presentation.apply(
        s5_rows,
        request,
        all_warnings,
        unrebalanced_total,
        practical=practical,
    )
