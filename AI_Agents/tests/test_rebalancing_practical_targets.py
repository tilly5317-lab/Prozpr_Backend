"""Spec 2026-09-15 §2 — rebalancing targets come from the practical allocation.

`_assign_subgroup_targets` already reads `practical.aggregated_subgroups`, but
`step5_aggregation` DROPS rows whose total is zero, so a subgroup the practical
plan does not want is ABSENT rather than present as `0`. The pass-through at the
top of the row loop read absent as "no practical opinion" and kept whatever the
input builder seeded — which is the IDEAL allocation's number
(`rebal_engine/service.py`, `cached_output`). Result: a buy into a subgroup the
practical plan gives nothing to, and per-subgroup targets that no longer sum to
the practical allocation shipped on the same payload.

Absent must mean ZERO. Frozen subgroups (ELSS, non-MF equity) are the deliberate
exception: they are absent from the target map by construction and surfaced from
`corpus_breakdown` in step6 instead.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402

# On the reference profile the IDEAL engine funds these two equity subgroups and
# the PRACTICAL engine funds neither — so the input builder seeds a rank-1 target
# the practical plan never asked for. Values pinned from a real run 2026-09-15.
_IDEAL_ONLY_MEDIUM_BETA = Decimal("951500")
_IDEAL_ONLY_HIGH_BETA = Decimal("543700")


def _practical():
    from practical_asset_allocation.pipeline import run_practical_allocation

    return run_practical_allocation(make_practical_input())


def _row(subgroup, rank, pre_cap, present="0", is_recommended=True):
    from Rebalancing.models import FundRowInput

    return FundRowInput(
        asset_subgroup=subgroup,
        sub_category="Fund",
        recommended_fund=f"{subgroup} r{rank}",
        isin=f"{subgroup.upper()}_{rank}",
        rank=rank,
        target_amount_pre_cap=Decimal(pre_cap),
        present_allocation_inr=Decimal(present),
        invested_cost_inr=Decimal(present),
        lt_value_inr=Decimal(present),
        lt_cost_inr=Decimal(present),
        current_nav=Decimal("100"),
        fund_rating=8,
        is_recommended=is_recommended,
    )


def _assign(rows, practical=None, n_funds=1):
    from Rebalancing.pipeline import _assign_subgroup_targets

    out = _assign_subgroup_targets(rows, practical or _practical(), 100, n_funds)
    return {r.isin: r for r in out}


def test_a_subgroup_absent_from_the_practical_plan_gets_a_zero_target():
    """The defect. The ideal-seeded ₹9.51L must not survive into the plan."""
    by = _assign([_row("medium_beta_equities", 1, _IDEAL_ONLY_MEDIUM_BETA)])

    assert by["MEDIUM_BETA_EQUITIES_1"].target_amount_pre_cap == Decimal(0)


def test_an_absent_subgroup_gets_no_protected_floor_either():
    """A held row in an absent subgroup must not reserve what it holds — the
    practical plan wants nothing there, so the whole holding is free to move."""
    by = _assign(
        [_row("medium_beta_equities", 1, _IDEAL_ONLY_MEDIUM_BETA, present="400000")]
    )

    row = by["MEDIUM_BETA_EQUITIES_1"]
    assert row.target_amount_pre_cap == Decimal(0)
    assert row.protected_floor_inr == Decimal(0)


def test_a_subgroup_the_practical_plan_funds_is_unchanged():
    """Guard: the fix must only touch subgroups the practical plan omits."""
    practical = _practical()
    low_beta = next(
        r.total for r in practical.aggregated_subgroups if r.subgroup == "low_beta_equities"
    )
    by = _assign([_row("low_beta_equities", 1, "1")], practical)

    assert by["LOW_BETA_EQUITIES_1"].target_amount_pre_cap == Decimal(str(low_beta))


def test_frozen_subgroups_still_pass_through_untouched():
    """ELSS / non-MF equity are absent from the target map BY DESIGN — step6
    surfaces them from `corpus_breakdown`. Zeroing them here would erase the
    frozen rows the ideal-vs-practical UI renders."""
    by = _assign(
        [
            _row("tax_efficient_equities", 1, "1000000"),
            _row("non_mf_equities", 1, "1000000"),
        ]
    )

    assert by["TAX_EFFICIENT_EQUITIES_1"].target_amount_pre_cap == Decimal("1000000")
    assert by["NON_MF_EQUITIES_1"].target_amount_pre_cap == Decimal("1000000")


def test_off_list_and_force_exit_rows_in_an_absent_subgroup_pass_through():
    """NEUTRAL rows (rank 0) carry their locked ST value as the target and
    force-exit rows carry 0 — neither is a goal target, so neither is ours
    to rewrite."""
    from Rebalancing.config import FORCE_EXIT_RANK

    neutral = _row("medium_beta_equities", 0, "250000", present="600000", is_recommended=False)
    forced = _row("medium_beta_equities", FORCE_EXIT_RANK, "0", present="80000", is_recommended=False)
    by = _assign([neutral, forced])

    assert by["MEDIUM_BETA_EQUITIES_0"].target_amount_pre_cap == Decimal("250000")
    assert by["MEDIUM_BETA_EQUITIES_9999"].target_amount_pre_cap == Decimal(0)


def _e2e_response():
    from Rebalancing.models import RebalancingComputeRequest
    from Rebalancing.pipeline import run_rebalancing

    rows = [
        _row("low_beta_equities", 1, "1", present="1500000"),
        _row("multi_asset", 1, "1", present="4000000"),
        _row("arbitrage_plus_income", 1, "1", present="6000000"),
        # Seeded from the IDEAL output by the input builder; the practical plan
        # funds neither.
        _row("medium_beta_equities", 1, _IDEAL_ONLY_MEDIUM_BETA, present="900000"),
        _row("high_beta_equities", 1, _IDEAL_ONLY_HIGH_BETA, present="0"),
    ]
    req = RebalancingComputeRequest(
        practical_allocation_input=make_practical_input(),
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=rows,
    )
    return run_rebalancing(req), _practical()


def test_no_buy_is_raised_into_a_subgroup_the_practical_plan_omits():
    resp, _ = _e2e_response()
    by_sg = {s.asset_subgroup: s for s in resp.subgroups}

    # Nothing held and nothing wanted — step6 drops the phantom row entirely.
    assert "high_beta_equities" not in by_sg
    # Held but unwanted — the row survives, at a zero target, and only sells.
    assert by_sg["medium_beta_equities"].goal_target_inr == Decimal(0)
    assert by_sg["medium_beta_equities"].total_buy_inr == Decimal(0)

    bought = {
        t.asset_subgroup
        for t in resp.trade_list
        if getattr(t, "action", None) == "BUY"
    }
    assert "medium_beta_equities" not in bought
    assert "high_beta_equities" not in bought


def test_per_subgroup_targets_sum_to_the_practical_allocation_on_the_payload():
    """The response ships the practical allocation alongside the trade list; the
    two must agree, or the customer is shown a plan and a set of trades that do
    not add up."""
    resp, practical = _e2e_response()

    frozen = {"tax_efficient_equities", "non_mf_equities"}
    engine_total = sum(
        (s.goal_target_inr for s in resp.subgroups if s.asset_subgroup not in frozen),
        Decimal(0),
    )
    # Only subgroups with an MF row in the request can carry a target; the
    # practical plan's other rows have nowhere to land. Compare like for like.
    present_sgs = {s.asset_subgroup for s in resp.subgroups}
    practical_total = sum(
        (
            Decimal(str(r.total))
            for r in practical.aggregated_subgroups
            if r.subgroup not in frozen and r.subgroup in present_sgs
        ),
        Decimal(0),
    )
    assert engine_total == practical_total
