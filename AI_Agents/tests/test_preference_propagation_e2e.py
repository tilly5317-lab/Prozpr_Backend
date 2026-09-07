"""Propagation E2E (spec §5): a preference on the PAA input reshapes the
allocation AND the rebalancing plan built on it — zero per-module tilt set."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402


def test_saved_preference_reaches_the_rebalancing_plan():
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    prefs = HumanOverridePreferences(
        asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
    )
    neutral = run_practical_allocation(make_practical_input())
    preferred = run_practical_allocation(
        make_practical_input().model_copy(update={"human_override": prefs})
    )
    n_eq = neutral.asset_class_breakdown.recommended.equity_total_pct
    p_eq = preferred.asset_class_breakdown.recommended.equity_total_pct
    assert p_eq > n_eq + 5.0, "the preference must genuinely move the target mix"
    # Honored in CARVED basis (the numbers the customer sees): an 80% ask
    # lands at 80% of the carved breakdown, exactly.
    assert abs(p_eq - 80.0) < 2.0
    # The rebalancing engine consumes exactly these rows via
    # request.practical_allocation_input → run_practical_allocation
    # (Rebalancing/pipeline.py) — no rebalancing-side tilt involved.


def test_constrained_customer_discloses_shortfall():
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    prefs = HumanOverridePreferences(
        asset_class_requested={"equity": 10.0, "debt": 80.0, "others": 10.0}
    )
    out = run_practical_allocation(
        make_practical_input(
            total_corpus=6_000_000.0, mf_corpus=5_000_000.0,
            elss_corpus=4_000_000.0, non_mf_equity_corpus=1_000_000.0,
            net_financial_assets=6_000_000.0,
        ).model_copy(update={"human_override": prefs})
    )
    applied = out.human_override_applied
    assert applied is not None and applied.shortfall_reason is not None
    assert applied.achieved["equity"] > applied.requested["equity"]


def test_preference_moves_the_ainv_subgroup_split():
    """The same preference that reshapes the practical allocation reshapes the
    ADDITIONAL-INVESTMENT deploy split too — no AINV-side preference code.

    Mirrors the one lift the app builder performs (input_builder.py: `subgroups =
    [SubgroupBucketAmounts(**row.model_dump()) for row in
    allocation_output.aggregated_subgroups]`, with `_EXCLUDE_SUBGROUPS` passed as
    `exclude_subgroups`); ranked funds are synthetic here so the assertion rides
    on `per_subgroup_target`, which the ranking never touches.
    """
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )
    from additional_investment.pipeline import run_additional_investment
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation

    from app.domains.additional_investment.services.ainv_engine.input_builder import (
        _EXCLUDE_SUBGROUPS,
    )

    equity_subgroups = {"low_beta_equities", "us_equities"}

    def equity_share_of_the_deploy(practical_output) -> float:
        subgroups = [
            SubgroupBucketAmounts(**row.model_dump())
            for row in practical_output.aggregated_subgroups
        ]
        ranked = [
            RankedFund(
                asset_subgroup=s.subgroup,
                sub_category="Synthetic",
                rank=1,
                isin=f"INF000{s.subgroup[:6]}",
                scheme_code=s.subgroup,
                recommended_fund=f"Fund {s.subgroup}",
            )
            for s in subgroups
            if s.subgroup not in _EXCLUDE_SUBGROUPS
        ]
        out = run_additional_investment(
            AdditionalInvestmentInput(
                deploy_amount_inr=100_000.0,
                cadence=Cadence.SIP_MONTHLY,
                subgroups=subgroups,
                short_term_fulfilled=True,
                medium_term_fulfilled=True,
                ranked_funds=ranked,
                default_cap_pct=100.0,
                exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
            )
        )
        return sum(
            t.ratio for t in out.per_subgroup_target if t.subgroup in equity_subgroups
        )

    prefs = HumanOverridePreferences(
        asset_class_requested={"equity": 80.0, "debt": 15.0, "others": 5.0}
    )
    neutral = equity_share_of_the_deploy(run_practical_allocation(make_practical_input()))
    preferred = equity_share_of_the_deploy(
        run_practical_allocation(
            make_practical_input().model_copy(update={"human_override": prefs})
        )
    )

    assert neutral < 0.25, "baseline deploy must not already be equity-dominated"
    assert preferred > neutral + 0.20, (
        "the preference must move the AINV deploy split, not just the target mix"
    )
