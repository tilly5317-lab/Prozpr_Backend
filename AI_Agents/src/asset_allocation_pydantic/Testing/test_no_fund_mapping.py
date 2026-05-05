"""Regression: customer-facing GoalAllocationOutput exposes only equity/debt/others.

After dropping ``FUND_MAPPING``, no fund-name / ISIN / SEBI sub-category strings
should appear in the pipeline output. Asset-class breakdown is the canonical
customer view; per-bucket equity/debt/others must be populated for at least
short / medium / long term whenever those buckets carry money.
"""
from __future__ import annotations

import json

import pytest

from asset_allocation_pydantic import AllocationInput, Goal, run_allocation
from asset_allocation_pydantic.steps._rationale_llm import no_llm_rationale_fn


def _profile_with_long_term_goal() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=6.0,
        age=35,
        annual_income=2_500_000,
        osi=0.4,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=10_000_000,
        monthly_household_expense=80_000,
        tax_regime="new",
        effective_tax_rate=30.0,
        goals=[
            Goal(
                goal_name="Retirement",
                time_to_goal_months=300,
                amount_needed=50_000_000,
                goal_priority="non_negotiable",
            ),
        ],
    )


def _profile_with_mixed_horizons() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=5.0,
        age=42,
        annual_income=1_800_000,
        osi=0.5,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=8_000_000,
        monthly_household_expense=70_000,
        tax_regime="new",
        effective_tax_rate=20.0,
        goals=[
            Goal(
                goal_name="Car",
                time_to_goal_months=18,
                amount_needed=900_000,
                goal_priority="negotiable",
            ),
            Goal(
                goal_name="Home down payment",
                time_to_goal_months=48,
                amount_needed=2_500_000,
                goal_priority="non_negotiable",
            ),
            Goal(
                goal_name="Retirement",
                time_to_goal_months=240,
                amount_needed=40_000_000,
                goal_priority="non_negotiable",
            ),
        ],
    )


@pytest.fixture(params=[_profile_with_long_term_goal, _profile_with_mixed_horizons])
def output(request):
    return run_allocation(request.param(), rationale_fn=no_llm_rationale_fn)


_FORBIDDEN_KEYS = {
    "fund_mapping",
    "fund_mappings",
    "recommended_fund",
    "isin",
    "sub_category",
}


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def test_no_fund_or_sebi_keys_in_output(output):
    payload = json.loads(output.model_dump_json())
    leaked = _FORBIDDEN_KEYS.intersection(_walk_keys(payload))
    assert not leaked, f"customer-facing output leaks: {sorted(leaked)}"


def test_asset_class_breakdown_is_populated(output):
    acb = output.asset_class_breakdown
    assert acb is not None
    bucket_names = {b.bucket for b in acb.actual.per_bucket}
    assert {"emergency", "short_term", "medium_term", "long_term"} <= bucket_names
    grand = acb.actual.equity_total + acb.actual.debt_total + acb.actual.others_total
    assert grand > 0


def test_aggregated_subgroups_have_only_subgroup_keys(output):
    for row in output.aggregated_subgroups:
        dumped = row.model_dump()
        assert set(dumped.keys()) == {
            "subgroup",
            "emergency",
            "short_term",
            "medium_term",
            "long_term",
            "total",
        }
