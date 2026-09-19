"""Unit tests for build_constraint_impact — comply-and-caution deviation block."""

from __future__ import annotations

from decimal import Decimal

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys  # noqa: E402
from Rebalancing.consolidation import (  # noqa: E402
    ConsolidationConstraints,
    reshape_response,
)

from app.domains.rebalancing.services.rebal_engine.constraint_impact import (  # noqa: E402
    build_constraint_impact,
)


def test_buy_mix_shows_shift_when_asset_class_flat():
    # 50/50 largecap/debt buys; "only largecap" → 100% largecap. Asset-class mix
    # is degenerate on the synthetic fixture, so the FINE lens must carry it.
    original = minimal_response_with_buys(
        buys=[("A", "Large Cap Fund", 1, 50000),
              ("B", "Short Duration Fund", 1, 50000)],
        sells=[])
    reshaped, err = reshape_response(
        original, ConsolidationConstraints(allowed_categories=("Large Cap Fund",)))
    assert err is None
    impact = build_constraint_impact(original, reshaped, risk_profile="Moderate")

    assert impact["risk_profile"] == "Moderate"
    mix = impact["buy_mix_by_category"]
    assert mix["unconstrained"]["Large Cap Fund"] == 50.0
    assert mix["unconstrained"]["Short Duration Fund"] == 50.0
    assert mix["constrained"] == {"Large Cap Fund": 100.0}
    assert sum(mix["constrained"].values()) == 100.0


def test_target_mix_read_from_real_breakdown():
    resp = minimal_response_with_buys(
        buys=[("A", "Large Cap Fund", 1, 50000)], sells=[])
    reshaped, _ = reshape_response(resp, ConsolidationConstraints(target_fund_count=1))
    impact = build_constraint_impact(resp, reshaped, risk_profile=None)
    # target mix comes from practical_allocation.asset_class_breakdown.planned
    tgt = impact["target_mix_pct"]
    assert set(tgt) == {"equity", "debt", "others"}
    assert all(isinstance(v, float) for v in tgt.values())
    assert sum(tgt.values()) > 0
    assert "largest_deviations" in impact
