"""SIP fidelity under a stated preference — the acceptance test for
docs/superpowers/specs/2026-09-20-sip-follows-requested-split-design.md.

Runs the REAL practical allocation and the REAL additional-investment engine
against the live fund ranking, at the bucket Task 1's branch selects. Asserts on
the look-through class rollup, never on raw subgroup buys: multi_asset is a
hybrid the customer-facing bars unbundle 65/25/10, so a correctly-honoured
50/30/20 plan sums RAW to roughly 25/20/55.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402

TOLERANCE_PP = 0.25
SIP_INR = 25_000.0
_CLASS_KEY = {"Equity": "equity", "Debt": "debt", "Others": "others"}


def _run_sip(class_mix, pins, corpus, sip_inr=SIP_INR):
    """Stated preference -> the realised SIP.

    Returns (class_pct, subgroup_pct, deployed_inr).
    """
    from additional_investment.models import (
        AdditionalInvestmentInput, Cadence, RankedFund, SubgroupBucketAmounts,
    )
    from additional_investment.pipeline import run_additional_investment
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation
    from Rebalancing.config import AINV_SIP_FUND_CAP_FLOOR_INR, OTHERS_FUND_CAP_PCT
    from Rebalancing.tables import cap_pct_for

    from app.domains.additional_investment.services.additional_investment_read_service import (
        build_ainv_asset_class_breakdown,
    )
    from app.domains.additional_investment.services.ainv_engine.input_builder import (
        _EXCLUDE_SUBGROUPS,
    )
    from app.domains.profile.services.screen_preference_service import (
        resolve_screen_preferences,
    )
    from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

    resolved = resolve_screen_preferences(class_mix, pins)
    prefs = HumanOverridePreferences(
        asset_class_requested=resolved.asset_class_requested,
        subgroup_emphasis=resolved.subgroup_emphasis,
    )
    practical = run_practical_allocation(
        make_practical_input(
            total_corpus=corpus, mf_corpus=corpus,
            non_mf_equity_corpus=0.0, elss_corpus=0.0,
            net_financial_assets=corpus,
        ).model_copy(update={"human_override": prefs})
    )
    subgroups = [
        SubgroupBucketAmounts(**row.model_dump())
        for row in practical.aggregated_subgroups
    ]
    ranked = [
        RankedFund(
            asset_subgroup=rr.asset_subgroup, sub_category=rr.sub_category,
            rank=rr.rank, isin=rr.isin, scheme_code=rr.scheme_code,
            recommended_fund=rr.fund_name,
        )
        for rows in get_fund_ranking().values() for rr in rows
    ]
    out = run_additional_investment(
        AdditionalInvestmentInput(
            deploy_amount_inr=sip_inr,
            cadence=Cadence.SIP_MONTHLY,
            subgroups=subgroups,
            # What Task 1's branch forces for a preference SIP.
            short_term_fulfilled=True,
            medium_term_fulfilled=True,
            ranked_funds=ranked,
            cap_pct_by_subgroup={
                s.subgroup: cap_pct_for(s.subgroup)
                for s in subgroups if s.subgroup not in _EXCLUDE_SUBGROUPS
            },
            default_cap_pct=OTHERS_FUND_CAP_PCT,
            exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
            sip_fund_cap_floor_inr=AINV_SIP_FUND_CAP_FLOOR_INR,
        )
    )
    deployed = float(out.deployed_inr)
    breakdown = build_ainv_asset_class_breakdown(
        (b.asset_subgroup, b.sub_category, float(b.amount_inr)) for b in out.buys
    )
    class_pct = {"equity": 0.0, "debt": 0.0, "others": 0.0}
    if breakdown is not None and breakdown.target_total_inr > 0:
        for row in breakdown.rows:
            class_pct[_CLASS_KEY[row.asset_class]] = (
                row.target_inr / breakdown.target_total_inr * 100.0
            )
    subgroup_pct: dict[str, float] = {}
    for b in out.buys:
        subgroup_pct[b.asset_subgroup] = (
            subgroup_pct.get(b.asset_subgroup, 0.0)
            + float(b.amount_inr) / deployed * 100.0
        )
    return class_pct, subgroup_pct, deployed


@pytest.mark.parametrize("class_mix", [
    {"equity": 50.0, "debt": 30.0, "others": 20.0},
    {"equity": 80.0, "debt": 15.0, "others": 5.0},
    {"equity": 30.0, "debt": 60.0, "others": 10.0},
])
def test_case1_class_only_preference_reaches_the_sip(class_mix):
    class_pct, _, deployed = _run_sip(class_mix, pins=[], corpus=5_000_000.0)
    for cls, stated in class_mix.items():
        assert abs(class_pct[cls] - stated) <= TOLERANCE_PP, (
            f"{cls}: stated {stated}, realised {class_pct[cls]:.3f}"
        )
    assert deployed == pytest.approx(SIP_INR, abs=200.0)


def test_case2_subcategory_preference_reaches_the_sip():
    class_mix = {"equity": 50.0, "debt": 30.0, "others": 20.0}
    asked = {
        "low_beta_equities": 25.0, "medium_beta_equities": 15.0,
        "us_equities": 10.0, "arbitrage_plus_income": 30.0,
        "gold_commodities": 20.0,
    }
    pins = [{"subgroup": sg, "pct_of_total": p} for sg, p in asked.items()]
    _, subgroup_pct, deployed = _run_sip(class_mix, pins, corpus=5_000_000.0)
    for sg, stated in asked.items():
        assert abs(subgroup_pct.get(sg, 0.0) - stated) <= TOLERANCE_PP, (
            f"{sg}: stated {stated}, realised {subgroup_pct.get(sg, 0.0):.3f}"
        )
    assert deployed == pytest.approx(SIP_INR, abs=200.0)


def test_case2_a_typed_zero_buys_nothing():
    class_mix = {"equity": 50.0, "debt": 30.0, "others": 20.0}
    asked = {
        "low_beta_equities": 50.0, "us_equities": 0.0,
        "arbitrage_plus_income": 30.0, "gold_commodities": 20.0,
    }
    pins = [{"subgroup": sg, "pct_of_total": p} for sg, p in asked.items()]
    _, subgroup_pct, _ = _run_sip(class_mix, pins, corpus=5_000_000.0)
    assert subgroup_pct.get("us_equities", 0.0) == 0.0
