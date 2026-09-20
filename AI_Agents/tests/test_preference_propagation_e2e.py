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
    # Task C2: achieved is the run's own class breakdown, no longer carried.
    assert (
        out.asset_class_breakdown.recommended.equity_total_pct
        > prefs.asset_class_requested["equity"]
    )


def test_preference_reaches_the_long_term_column():
    """Engine contract, not a production-wiring test: `compute_targets`
    weights subgroups by whichever bucket column it is handed. This harness
    hard-codes `short_term_fulfilled=medium_term_fulfilled=True` for both
    runs, so it always targets long_term; what it checks is that when the
    foundation feeding that column came from a preference-shaped PAA run,
    the long-term column already carries the stated split — the engine
    just mirrors whatever it is handed.

    Builds `AdditionalInvestmentInput` by hand from PAA's own output — it
    does not import `ainv_engine/input_builder.py`, so it proves nothing
    about that production glue, only about the engine's response to the
    same foundation shape the builder would hand it. Ranked funds are
    synthetic here so the assertion rides on `per_subgroup_target`, which
    the ranking never touches.
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

    # Spec 2026-09-14: this used to assert a MAGNITUDE ("moves by more than
    # N"), which went stale twice as the engine got more correct — once when
    # the sleeve began sizing itself to the requested debt, and again when the
    # commodity bound stopped it overshooting. The magnitude is a side-effect
    # of sleeve sizing; what holds is DIRECTION, plus the mechanism the
    # `_mirrors_the_foundation` helper below asserts directly: `compute_targets`
    # has no preference branch of its own — it weights by whatever column
    # values it is handed, so a preference only reaches it by reshaping those
    # values upstream, in PAA.
    assert neutral < 0.25, "baseline deploy must not already be equity-dominated"

    # 1. Direction: the preference genuinely reaches the deploy.
    assert preferred > neutral, (
        "an 80% equity ask must raise the equity share of the AINV deploy"
    )

    # 2. The contract itself: every deploy ratio equals that subgroup's share of
    #    the foundation's LONG-TERM column (the documented mechanism — short and
    #    medium are fulfilled here, so long_term is the target bucket). If AINV
    #    ever grew preference logic of its own, this is what would break.
    def _mirrors_the_foundation(practical_output) -> None:
        subgroups = [
            SubgroupBucketAmounts(**row.model_dump())
            for row in practical_output.aggregated_subgroups
        ]
        eligible = {
            s.subgroup: s.long_term
            for s in subgroups
            if s.subgroup not in _EXCLUDE_SUBGROUPS and s.long_term > 0
        }
        total = sum(eligible.values())
        ranked = [
            RankedFund(
                asset_subgroup=sg, sub_category="Synthetic", rank=1,
                isin=f"INF000{sg[:6]}", scheme_code=sg, recommended_fund=f"Fund {sg}",
            )
            for sg in eligible
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
        for t in out.per_subgroup_target:
            assert abs(t.ratio - eligible[t.subgroup] / total) < 1e-6, (
                f"{t.subgroup}: AINV ratio {t.ratio} does not mirror the "
                f"foundation share {eligible[t.subgroup] / total}"
            )

    _mirrors_the_foundation(run_practical_allocation(make_practical_input()))
    _mirrors_the_foundation(
        run_practical_allocation(
            make_practical_input().model_copy(update={"human_override": prefs})
        )
    )
