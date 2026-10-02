"""₹100 rounding lives only in the final SIP / lump-sum / trade amounts; the
allocation engines work in whole rupees and conserve the corpus (2026-09-30).

Before this, every engine amount was rounded to ₹100 separately, so the ideal
engine's total missed the corpus by up to ₹150 and the multi-asset sleeve's
three slices failed to add back to the sleeve on ~1 input in 4.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402


def test_the_sleeve_slices_always_add_back_to_the_sleeve():
    from asset_allocation_pydantic.models import MultiAssetFundComposition
    from asset_allocation_pydantic.steps.step4_long_term import phase4_multi_asset

    rng = random.Random(30)
    comp = MultiAssetFundComposition(equity_pct=65, debt_pct=25, others_pct=10)
    for _ in range(3000):
        b = phase4_multi_asset(
            rng.randrange(1, 50_000_000),
            rng.randrange(1, 20_000_000),
            rng.randrange(0, 10_000_000),
            comp,
        )
        assert b.equity_component + b.debt_component + b.others_component == b.multi_asset_amount


def _random_inputs(n):
    rng = random.Random(11)
    for _ in range(n):
        # Above the ₹3L emergency fund, where over-allocating is by design.
        # Floor at 350k so even the worst-case ELSS draw (10% of corpus)
        # still leaves the rebalancing corpus above that floor.
        corpus = rng.uniform(350_000, 50_000_000)
        elss = rng.choice([0.0, rng.uniform(0, corpus * 0.1)])
        yield make_practical_input(
            total_corpus=corpus,
            elss_corpus=elss,
            effective_risk_score=rng.choice([1, 2.5, 4, 5.5, 7, 8.5, 9.5, 10]),
        )


def test_the_ideal_engine_allocates_the_corpus_to_the_rupee():
    from asset_allocation_pydantic.models import AllocationInput
    from asset_allocation_pydantic.pipeline import run_allocation

    for inp in _random_inputs(150):
        ideal_inp = AllocationInput(**{k: getattr(inp, k) for k in AllocationInput.model_fields})
        out = run_allocation(ideal_inp)
        assert out.grand_total == int(inp.total_corpus)


def test_the_practical_engine_allocates_the_corpus_to_the_rupee():
    """Within ₹1: the fractional ELSS input is taken to whole rupees on its
    own, which can leave a paise-driven rupee."""
    from practical_asset_allocation.pipeline import run_practical_allocation

    for inp in _random_inputs(150):
        out = run_practical_allocation(inp)
        assert abs(out.grand_total - int(inp.total_corpus)) <= 1


def _deploy(cadence, goal_share, deploy=50_000.0):
    from additional_investment import AdditionalInvestmentInput, run_additional_investment
    from additional_investment.models import Cadence, RankedFund, SubgroupBucketAmounts

    return run_additional_investment(
        AdditionalInvestmentInput(
            deploy_amount_inr=deploy,
            cadence=Cadence(cadence),
            subgroups=[
                SubgroupBucketAmounts(subgroup="short_debt", short_term=1.0, total=1.0),
                SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=1.0, total=1.0),
            ],
            goal_share_inr=goal_share,
            goal_subgroup="short_debt",
            current_value_by_subgroup={} if cadence == "lumpsum" else None,
            ranked_funds=[
                RankedFund(asset_subgroup=sg, sub_category="Fund", rank=1, isin=sg,
                           scheme_code=sg, recommended_fund=sg)
                for sg in ("short_debt", "low_beta_equities")
            ],
        )
    )


def _goal_buy(out):
    return next(b.amount_inr for b in out.buys if b.asset_subgroup == "short_debt")


def test_the_sip_goal_share_rounds_up_so_the_goal_is_never_short():
    """₹2L over a 6-month window is ₹33,333.33 a month. The engine hands over
    ₹33,334; nearest-₹100 would buy ₹33,300 and leave the goal ₹200 short."""
    assert _goal_buy(_deploy("sip_monthly", 33_334.0)) == 33_400


def test_the_sip_goal_share_never_exceeds_the_sip():
    assert _goal_buy(_deploy("sip_monthly", 49_950.0)) == 50_000
    assert _goal_buy(_deploy("sip_monthly", 12_345.0, deploy=12_345.0)) == 12_300
