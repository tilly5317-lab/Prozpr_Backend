"""build_rebal_facts_pack carries constraint_impact when provided (else absent)."""

from __future__ import annotations

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys  # noqa: E402

from app.domains.rebalancing.services.rebal_engine.service import (  # noqa: E402
    build_rebal_facts_pack,
)


def test_facts_pack_carries_constraint_impact_when_given():
    resp = minimal_response_with_buys(buys=[("A", "Large Cap Fund", 1, 100)], sells=[])
    pack = build_rebal_facts_pack(resp, constraint_impact={"risk_profile": "Moderate"})
    assert pack["constraint_impact"] == {"risk_profile": "Moderate"}


def test_facts_pack_omits_constraint_impact_by_default():
    resp = minimal_response_with_buys(buys=[("A", "Large Cap Fund", 1, 100)], sells=[])
    pack = build_rebal_facts_pack(resp)
    assert "constraint_impact" not in pack
