"""Unit tests for asset_allocation/service.py: facts pack + fallback brief."""

from __future__ import annotations

import json

import pytest

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from asset_allocation_pydantic import AllocationInput, Goal, run_allocation  # type: ignore[import-not-found]
from asset_allocation_pydantic.steps._rationale_llm import no_llm_rationale_fn  # type: ignore[import-not-found]

from app.services.ai_bridge.asset_allocation.service import (
    build_aa_facts_pack,
    build_fallback_brief,
)


@pytest.fixture
def sample_output():
    inp = AllocationInput(
        effective_risk_score=5.5,
        age=39,
        annual_income=2_000_000,
        osi=0.4,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=8_000_000,
        monthly_household_expense=80_000,
        tax_regime="new",
        effective_tax_rate=30.0,
        goals=[
            Goal(
                goal_name="Retirement",
                time_to_goal_months=240,
                amount_needed=40_000_000,
                goal_priority="non_negotiable",
            ),
        ],
    )
    return run_allocation(inp, rationale_fn=no_llm_rationale_fn)


def test_facts_pack_is_a_plain_dict(sample_output):
    pack = build_aa_facts_pack(sample_output)
    assert isinstance(pack, dict)
    assert pack  # non-empty


def test_facts_pack_contains_expected_top_level_keys(sample_output):
    pack = build_aa_facts_pack(sample_output)
    assert "risk_score" in pack
    assert "total_corpus_inr" in pack
    assert "asset_class_mix_pct" in pack
    assert "by_horizon" in pack
    assert "goals" in pack


def test_facts_pack_omits_fund_and_isin(sample_output):
    pack = build_aa_facts_pack(sample_output)
    blob = json.dumps(pack).lower()
    for forbidden in ("isin", "recommended_fund", "fund_mapping", "sub_category"):
        assert forbidden not in blob, f"facts pack leaks {forbidden}"


def test_facts_pack_is_under_token_budget(sample_output):
    pack = build_aa_facts_pack(sample_output)
    # Rough upper bound: 1500 tokens ≈ 6000 characters as JSON.
    assert len(json.dumps(pack)) < 6000


def test_facts_pack_is_deterministic(sample_output):
    a = build_aa_facts_pack(sample_output)
    b = build_aa_facts_pack(sample_output)
    assert a == b


def test_fallback_brief_is_non_empty(sample_output):
    text = build_fallback_brief(sample_output, "full")
    assert text.strip()
    assert "goal-based allocation" in text.lower()


def test_facts_pack_does_not_contain_internal_subgroup_keys(sample_output):
    """Customer-tellable invariant: no internal subgroup names in the facts pack."""
    pack = build_aa_facts_pack(sample_output)
    blob = json.dumps(pack).lower()
    for forbidden in ("low_beta_equities", "high_beta_equities", "arbitrage_plus_income",
                      "tax_efficient_equities", "multi_asset"):
        assert forbidden not in blob, f"facts pack leaks internal subgroup key {forbidden}"


def test_facts_pack_has_indian_siblings_for_every_inr_field(sample_output):
    """Drift guard: every ``*_inr`` rupee key must have a matching ``*_indian``
    pre-formatted sibling so the chat formatter LLM never has to compute
    lakh/crore conversions (Haiku reliably gets these wrong by an order of
    magnitude).

    Walk the facts pack recursively. For each dict key ending in ``_inr``,
    assert a sibling key with the same prefix ending in ``_indian`` exists
    inside the same dict.
    """
    pack = build_aa_facts_pack(sample_output)

    def walk(node, path="root"):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("_inr"):
                    sibling = k[: -len("_inr")] + "_indian"
                    assert sibling in node, (
                        f"{path}: key {k!r} present but {sibling!r} sibling is missing"
                    )
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(pack)
