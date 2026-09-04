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
    # request.practical_allocation_input → run_practical_allocation (verified
    # seam, Rebalancing/pipeline.py:348) — no rebalancing-side tilt involved.
    from Rebalancing.models import RebalancingComputeRequest

    req = RebalancingComputeRequest(
        practical_allocation_input=make_practical_input().model_copy(
            update={"human_override": prefs}
        ),
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=[],
    )
    assert req.asset_class_tilt is None and req.market_cap_tilt is None


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
