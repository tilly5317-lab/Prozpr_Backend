"""Fresh money: goal money first, then the long-term plan."""

from __future__ import annotations

from additional_investment.models import (
    AdditionalInvestmentInput,
    Cadence,
    SubgroupBucketAmounts,
    TargetBucket,
)
from additional_investment.pipeline import run_additional_investment


def _row(sg, short=0.0, long=0.0):
    return SubgroupBucketAmounts(subgroup=sg, short_term=short, long_term=long, total=short + long)


def _inp(rows, deploy, cadence=Cadence.SIP_MONTHLY, **kw):
    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy, cadence=cadence, subgroups=rows, ranked_funds=[], **kw
    )


def _targets(out):
    return {t.subgroup: round(t.target_inr) for t in out.per_subgroup_target}


def test_sip_goal_share_goes_to_the_named_subgroup_even_with_no_short_term_row():
    rows = [_row("low_beta_equities", long=600_000), _row("medium_beta_equities", long=400_000)]
    out = run_additional_investment(_inp(rows, 50_000, goal_share_inr=30_000, goal_subgroup="arbitrage"))
    assert _targets(out) == {"arbitrage": 30_000, "low_beta_equities": 12_000, "medium_beta_equities": 8_000}
    assert out.target_bucket is TargetBucket.SHORT_TERM


def test_sip_without_a_goal_share_follows_the_long_term_plan():
    rows = [_row("arbitrage", short=500_000), _row("low_beta_equities", long=750_000),
            _row("medium_beta_equities", long=250_000)]
    out = run_additional_investment(_inp(rows, 20_000))
    assert _targets(out) == {"low_beta_equities": 15_000, "medium_beta_equities": 5_000}
    assert out.target_bucket is TargetBucket.LONG_TERM


def test_goal_subgroup_also_in_the_long_term_plan_gets_one_target():
    rows = [_row("short_debt", short=100_000, long=100_000), _row("low_beta_equities", long=300_000)]
    out = run_additional_investment(_inp(rows, 10_000, goal_share_inr=4_000, goal_subgroup="short_debt"))
    assert _targets(out) == {"short_debt": 5_500, "low_beta_equities": 4_500}


def test_lumpsum_goal_share_first_then_long_term_deficits_only():
    rows = [_row("arbitrage", short=200_000), _row("low_beta_equities", long=600_000)]
    out = run_additional_investment(_inp(
        rows, 300_000, cadence=Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 400_000},
        goal_share_inr=100_000, goal_subgroup="arbitrage",
    ))
    assert _targets(out) == {"arbitrage": 100_000, "low_beta_equities": 200_000}
    assert out.target_bucket is TargetBucket.LONG_TERM


def test_goal_share_is_capped_at_the_deploy_amount():
    rows = [_row("low_beta_equities", long=1_000_000)]
    out = run_additional_investment(_inp(rows, 10_000, goal_share_inr=25_000, goal_subgroup="arbitrage"))
    assert _targets(out) == {"arbitrage": 10_000}


def test_lumpsum_deficit_fill_and_sip_long_term_plan_diverge_on_the_same_holdings_map():
    """Two long-term rows at different holdings, so lumpsum deficit-fill and the SIP long-term split diverge."""
    rows =[_row("arbitrage", short=200_000), _row("low_beta_equities", long=600_000),
            _row("medium_beta_equities", long=400_000)]
    current = {"low_beta_equities": 600_000, "medium_beta_equities": 200_000}

    lumpsum_out = run_additional_investment(_inp(
        rows, 300_000, cadence=Cadence.LUMPSUM,
        current_value_by_subgroup=current,
        goal_share_inr=100_000, goal_subgroup="arbitrage",
    ))
    # low_beta is already at its long-term ideal (deficit 0); medium_beta's
    # whole 200k gap absorbs all of the post-goal 200k.
    assert _targets(lumpsum_out) == {"arbitrage": 100_000, "medium_beta_equities": 200_000}

    sip_out = run_additional_investment(_inp(
        rows, 300_000, cadence=Cadence.SIP_MONTHLY,
        current_value_by_subgroup=current,
        goal_share_inr=100_000, goal_subgroup="arbitrage",
    ))
    # SIP ignores the map entirely; the long-term column (600:400) splits the
    # post-goal 200k instead -- a different pair of recipients and amounts.
    assert _targets(sip_out) == {
        "arbitrage": 100_000, "low_beta_equities": 120_000, "medium_beta_equities": 80_000,
    }
